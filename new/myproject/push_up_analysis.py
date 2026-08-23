
import os
import re
import json
from typing import List, Optional, Sequence, Tuple

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

# ==============================================================================
# 0. 全局配置（Gemini模型与权重路径）
# ==============================================================================
GEMINI_MODEL = "gemini-2.5-flash"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_PATH = os.path.join(BASE_DIR, "push_up_modal.pth")
MAX_LANDMARKS = 33
MODEL_CACHE: dict = {"model": None, "template": None}

# ==============================================================================
# 2. 模型结构定义（与训练阶段v2.0完全一致，确保加载权重兼容）
# 核心思路：LSTM提取时序依赖特征 + TCN提取局部结构特征 → 融合后多任务评估
# ==============================================================================

# ------------------------------ LSTM编码器 ------------------------------
# 功能：处理变长动作序列，提取时序依赖特征（如动作先后顺序、节奏变化）
class LSTMEncoder(nn.Module):
    def __init__(self, input_dim=99, hidden=128, embed_dim=256):
        super().__init__()  # 继承nn.Module的初始化方法
        # 双向LSTM层：input_dim(输入维度99=33个关键点×3坐标)，hidden(隐藏层维度)，2层堆叠
        # batch_first=True：输入格式为[batch, seq_len, input_dim]
        # dropout=0.3：防止过拟合，隐藏层输出dropout
        self.lstm = nn.LSTM(input_dim, hidden, 2,
                            bidirectional=True, batch_first=True, dropout=0.3)
        # 全连接层：将双向LSTM的输出（hidden×2）映射到指定嵌入维度
        self.fc = nn.Linear(hidden * 2, embed_dim)

    def forward(self, x, lengths):
        # 步骤1：对序列长度排序（降序），适配pack_padded_sequence的要求
        lengths_sorted, idx = torch.sort(lengths, descending=True)
        x = x[idx]  # 按长度排序后的索引重新排列输入

        # 步骤2：打包变长序列（忽略padding部分，提升计算效率）
        packed = nn.utils.rnn.pack_padded_sequence(
            x, lengths_sorted.cpu(), batch_first=True
        )
        # 步骤3：LSTM前向传播，输出为打包后的序列特征和隐藏状态
        out, _ = self.lstm(packed)
        # 步骤4：解包序列，恢复为padding后的张量格式
        out, _ = nn.utils.rnn.pad_packed_sequence(out, batch_first=True)

        # 步骤5：提取每个序列的最后一个有效时间步特征（LSTM核心输出）
        B = out.size(0)  # batch_size
        # 构造最后一个有效时间步的索引（长度-1）
        last = (lengths_sorted - 1).view(B, 1, 1).expand(B, 1, out.size(2))
        feat = out.gather(1, last).squeeze(1)  # 按索引提取特征并压缩维度

        # 步骤6：恢复原始序列顺序（与输入顺序一致）
        _, inv = idx.sort()
        return self.fc(feat[inv])  # 全连接层映射后输出

# ------------------------------ TCN编码器 ------------------------------
# 功能：提取动作的局部结构特征（如关节角度、身体姿态的空间关系）
class TCNEncoder(nn.Module):
    def __init__(self, input_dim=99, channels=[128, 128, 128], embed_dim=256):
        super().__init__()
        layers = []  # 存储TCN的卷积层和激活层
        in_ch = input_dim  # 初始输入通道数=特征维度99
        # 构建3层1D卷积（捕捉局部时序结构）
        for out_ch in channels:
            # 1D卷积：kernel_size=3（感受野3个时间步），padding=1（保持序列长度）
            layers.append(nn.Conv1d(in_ch, out_ch, kernel_size=3, padding=1))
            layers.append(nn.ReLU())  # ReLU激活函数，引入非线性
            in_ch = out_ch  # 更新输入通道数为当前输出通道数
        self.net = nn.Sequential(*layers)  # 组合层为序列模型
        # 全连接层：将TCN输出的特征映射到指定嵌入维度
        self.fc = nn.Linear(channels[-1], embed_dim)

    def forward(self, x, lengths=None):
        # 转换维度：[batch, seq_len, input_dim] → [batch, input_dim, seq_len]（适配1D卷积）
        x = x.transpose(1, 2)
        out = self.net(x)  # TCN前向传播，输出[batch, channels[-1], seq_len]
        feat = out.mean(dim=2)  # 时间维度平均池化，得到全局局部特征
        return self.fc(feat)  # 全连接层映射后输出

# ------------------------------ 重构解码器 ------------------------------
# 功能：根据融合特征重构原始动作序列，通过重构误差评估动作平滑度
class ReconstructionDecoder(nn.Module):
    def __init__(self, embed_dim=512, pose_dim=99, hidden=128):
        super().__init__()
        # 初始化LSTM的隐藏态h0和细胞态c0（从融合嵌入特征映射得到）
        self.init_h = nn.Linear(embed_dim, hidden)
        self.init_c = nn.Linear(embed_dim, hidden)
        # LSTM解码器：输入为动作帧特征，输出为预测的下一帧特征
        self.lstm = nn.LSTM(pose_dim, hidden, batch_first=True)
        # 全连接层：将LSTM输出映射回原始动作特征维度（99）
        self.fc = nn.Linear(hidden, pose_dim)

    def forward(self, embed, x, lengths):
        B, T_max, _ = x.shape  # B=batch_size, T_max=最大序列长度
        # 从融合嵌入初始化LSTM的隐藏态和细胞态
        h0 = self.init_h(embed).unsqueeze(0)  # [1, B, hidden]
        c0 = self.init_c(embed).unsqueeze(0)  # [1, B, hidden]
        # LSTM前向传播（输入原始动作序列，输出重构序列）
        out, _ = self.lstm(x, (h0, c0))
        return self.fc(out)  # 映射回原始维度，输出重构序列

# ------------------------------ 下一帧预测器 ------------------------------
# 功能：根据融合特征预测后续k帧动作，辅助评估动作合理性（本版本未在评分中使用）
class NextFramePredictor(nn.Module):
    def __init__(self, embed_dim=512, output_dim=99, k=3):
        super().__init__()
        self.k = k  # 预测后续k帧
        # 全连接网络：从融合嵌入映射到k帧动作特征（output_dim×k）
        self.fc = nn.Sequential(
            nn.Linear(embed_dim, embed_dim),
            nn.ReLU(),
            nn.Linear(embed_dim, output_dim * k)
        )

    def forward(self, embed):
        B = embed.size(0)  # batch_size
        out = self.fc(embed)  # 输出形状[B, output_dim×k]
        return out.view(B, self.k, -1)  # 重塑为[B, k, output_dim]，即k帧动作

# ------------------------------ 动作质量二分类器 ------------------------------
# 功能：判断动作"好/坏"（本版本未在评分中使用，仅保留模型结构兼容）
class GoodBadClassifier(nn.Module):
    def __init__(self, embed_dim=512):
        super().__init__()
        # 全连接网络：从融合嵌入映射到0-1的概率（1=好动作，0=坏动作）
        self.fc = nn.Sequential(
            nn.Linear(embed_dim, 128),
            nn.ReLU(),
            nn.Linear(128, 1),
            nn.Sigmoid()  # Sigmoid激活，输出概率
        )

    def forward(self, embed):
        return self.fc(embed).squeeze(-1)  # 压缩最后一维，输出[B]

# ------------------------------ 融合质量模型（核心） ------------------------------
# 功能：整合LSTM+TCN编码器，实现多维度动作质量评估（标准度、平滑度等）
class FusionQualityModel(nn.Module):
    def __init__(self, input_dim=99, next_k=3):
        super().__init__()
        self.lstm_encoder = LSTMEncoder(input_dim)  # LSTM时序特征编码器
        self.tcn_encoder = TCNEncoder(input_dim)    # TCN局部结构特征编码器
        self.next_k = next_k  # 下一帧预测的帧数
        self.fusion_dim = 512  # 融合特征维度（256+256）

        # 辅助任务模块（重构、下一帧预测）
        self.decoder = ReconstructionDecoder(self.fusion_dim, pose_dim=input_dim)
        self.next_frame = NextFramePredictor(self.fusion_dim, output_dim=input_dim, k=next_k)
        self.classifier = GoodBadClassifier(self.fusion_dim)  # 二分类器（未使用）

    # 编码函数：输入动作序列，输出融合特征
    def encode(self, x, lengths):
        lstm_feat = self.lstm_encoder(x, lengths)  # LSTM提取的时序特征（256维）
        tcn_feat = self.tcn_encoder(x, lengths)    # TCN提取的局部特征（256维）
        return torch.cat([lstm_feat, tcn_feat], dim=1)  # 特征拼接（512维融合特征）

    # 前向传播：输入动作序列和长度，输出融合特征和二分类结果
    def forward(self, x, lengths):
        embed = self.encode(x, lengths)  # 编码得到融合特征
        cls = self.classifier(embed)     # 二分类预测（未使用）
        return embed, cls

# ==============================================================================
# 3. 加载预训练模型（核心步骤：确保模型权重和结构匹配）
# ==============================================================================
def get_quality_model(model_path: Optional[str] = None) -> Tuple[FusionQualityModel, torch.Tensor]:
    """延迟加载模型，避免导入即占用内存。"""
    path = model_path or MODEL_PATH
    cached_model = MODEL_CACHE.get("model")
    cached_template = MODEL_CACHE.get("template")
    if cached_model is not None and cached_template is not None:
        return cached_model, cached_template

    if not os.path.exists(path):
        raise FileNotFoundError(f"质量评估模型未找到: {path}")

    ckpt = torch.load(path, map_location="cpu")
    model = FusionQualityModel()
    model.load_state_dict(ckpt["model"], strict=True)
    model.eval()
    template = ckpt.get("good_template")
    if template is None:
        raise ValueError("模型权重中缺少 good_template")

    MODEL_CACHE["model"] = model
    MODEL_CACHE["template"] = template
    return model, template


def _sanitize_landmark(point: Optional[Sequence[float]]) -> List[float]:
    if (
        isinstance(point, (list, tuple))
        and len(point) == 3
        and all(isinstance(v, (float, int)) for v in point)
    ):
        return [float(point[0]), float(point[1]), float(point[2])]
    return [0.0, 0.0, 0.0]


def frames_to_tensor(frames: Sequence[Sequence[Sequence[float]]]) -> Tuple[torch.Tensor, torch.Tensor]:
    clean_frames: List[List[float]] = []
    for raw in frames:
        frame = list(raw) if isinstance(raw, (list, tuple)) else []
        if len(frame) < MAX_LANDMARKS:
            frame = frame + [None] * (MAX_LANDMARKS - len(frame))
        elif len(frame) > MAX_LANDMARKS:
            frame = frame[:MAX_LANDMARKS]

        row: List[float] = []
        for point in frame:
            row.extend(_sanitize_landmark(point))
        clean_frames.append(row)

    if not clean_frames:
        raise ValueError("动作帧为空，无法进行分析。")

    tensor = torch.tensor(clean_frames, dtype=torch.float32).unsqueeze(0)
    lengths = torch.tensor([len(clean_frames)], dtype=torch.long)
    return tensor, lengths

# ==============================================================================
# 4. 数据加载函数：读取JSON格式的动作帧数据，转换为模型输入格式
# ==============================================================================
def load_action(json_path):
    """
    从JSON文件中读取动作帧数据，处理为模型可接受的Tensor格式
    参数：json_path - JSON文件路径（支持.json/.jsonl格式）
    返回：x - 动作序列Tensor [1, seq_len, 99]（batch_size=1）
          length - 序列长度Tensor [1]
    异常：若未找到frames字段，抛出ValueError
    """
    # 读取文件内容（UTF-8编码，避免中文乱码）
    with open(json_path, "r", encoding="utf-8") as f:
        text = f.read().strip()

    # 正则表达式提取所有JSON对象（处理可能的多对象或格式不规范的文件）
    objs = re.findall(r"\{.*?\}", text, flags=re.S)
    o = None  # 存储包含frames字段的JSON对象
    for obj in objs:
        try:
            cand = json.loads(obj)  # 解析JSON对象
            if "frames" in cand:    # 找到包含动作帧数据的对象
                o = cand
                break
        except:
            continue  # 跳过解析失败的JSON对象

    # 若未找到frames字段，抛出异常
    if o is None:
        raise ValueError("❌ JSON 中未找到 frames 字段")

    frames = o["frames"]
    return frames_to_tensor(frames)

# ==============================================================================
# 5. 动作质量分析核心函数（v2.2版本：删除节奏一致性，优化权重分配）
# ==============================================================================

def clamp_score(v):
    """分数裁剪函数：将分数限制在0-100之间（避免异常值）"""
    return max(0.0, min(100.0, float(v)))

def reconstruction_error(model, x, length):
    """
    计算动作重构误差（评估动作平滑度）
    逻辑：模型根据融合特征重构原始动作，误差越小说明动作越平滑、规范
    """
    embed = model.encode(x, length)  # 编码得到融合特征
    recon = model.decoder(embed, x, length)  # 重构原始动作序列
    T = length.item()  # 实际序列长度（排除padding）
    # 计算重构序列与原始序列的MSE（均方误差）
    return ((recon[:, :T, :] - x[:, :T, :]) ** 2).mean().item()

def local_similarity(model, x, good_template, window=5):
    """
    计算局部动作相似度（评估局部稳定性）
    逻辑：滑动窗口（默认5帧）提取局部片段，与标准动作模板计算相似度，方差越小越稳定
    """
    sims = []  # 存储每个窗口的相似度
    T = x.size(1)  # 序列总长度
    # 若序列长度小于窗口大小，直接计算整个序列的相似度
    if T < window:
        embed = model.encode(x, torch.tensor([T]))
        sims.append(F.cosine_similarity(embed, good_template).item())
        return sims

    # 滑动窗口遍历序列，计算每个窗口的相似度
    for i in range(T - window + 1):
        clip = x[:, i:i+window, :]  # 提取窗口内的局部片段
        embed = model.encode(clip, torch.tensor([window]))  # 编码局部片段
        # 计算与标准动作模板的余弦相似度（-1~1，越大越相似）
        sims.append(F.cosine_similarity(embed, good_template).item())
    return sims

def tcn_smoothness(model, x):
    """
    计算TCN特征的平滑度（评估动作连贯性）
    逻辑：TCN特征的相邻时间步差异越小，说明动作越连贯、发力越均匀
    """
    x_tcn = x.transpose(1, 2)  # 转换维度适配TCN输入
    out = model.tcn_encoder.net(x_tcn)  # TCN特征提取
    # 计算相邻时间步的绝对差异均值（差异越小越连贯）
    return (out[:, :, 1:] - out[:, :, :-1]).abs().mean().item()

def full_quality_analysis(model, x, length, good_template):
    """
    多维度动作质量分析（核心函数）
    输出：全局标准度、重构平滑度、局部稳定性、动作连贯性、综合评分
    """
    # 编码得到动作的融合特征
    embed = model.encode(x, length)

    # ---- 1. 全局动作标准度（权重0.45）----
    # 计算当前动作与标准动作模板的余弦相似度（-1~1）
    global_sim = F.cosine_similarity(embed, good_template).item()
    # 转换为0-100分（相似度+1后除以2，映射到0-1，再×100）
    global_score = clamp_score(((global_sim + 1) / 2) * 100)

    # ---- 2. 重构平滑度（权重0.20）----
    recon_err = reconstruction_error(model, x, length)  # 重构误差
    # 误差越小分数越高，最大误差限制为0.1（超过0.1按0分算）
    recon_score = clamp_score(100 * (1 - min(recon_err, 0.1) / 0.1))

    # ---- 3. 局部稳定性（权重0.20）----
    sims = local_similarity(model, x, good_template)  # 局部相似度列表
    # 计算相似度的方差（方差越小，局部动作越稳定）
    local_var = float(np.var(sims)) if len(sims) > 1 else 0.0
    # 方差越小分数越高，最大方差限制为0.1（超过0.1按0分算）
    stability_score = clamp_score(100 * (1 - min(local_var, 0.1) / 0.1))

    # ---- 4. 动作连贯性（TCN）（权重0.15）----
    smooth = tcn_smoothness(model, x)  # TCN特征平滑度
    # 平滑度越小分数越高，最大平滑度限制为0.3（超过0.3按0分算）
    continuity_score = clamp_score(100 * (1 - min(smooth, 0.3) / 0.3))

    # ---- 新版综合评分（权重优化：提高全局标准度占比）----
    total_score = (
        0.45 * global_score +    # 全局标准度（核心权重）
        0.20 * recon_score +     # 重构平滑度
        0.20 * stability_score + # 局部稳定性
        0.15 * continuity_score  # 动作连贯性
    )

    # 打印多维度评分结果
    print("\n===== 多维度动作质量分析 (v2.2) =====")
    print(f"全局动作标准度    : {global_score:.2f}")
    print(f"动作平滑度(重构)  : {recon_score:.2f}")
    print(f"局部稳定性        : {stability_score:.2f}")
    print(f"动作连贯性(TCN)   : {continuity_score:.2f}")
    print(f"\n综合评分 (0-100)   : {total_score:.2f}")
    print("=================================\n")

    # 返回所有评分（用于Gemini生成报告）
    return (
        global_score,
        recon_score,
        stability_score,
        continuity_score,
        total_score
    )

# ==============================================================================
# 6. Gemini文本报告生成（v2.2：同步删除节奏一致性相关描述）
# 功能：调用Gemini大模型，基于多维度评分生成专业运动分析报告
# ==============================================================================
def gemini_analysis(scores, gemini_client):
    """
    调用Gemini生成专业动作分析报告
    参数：scores - 多维度评分元组（全局标准度、平滑度、稳定性、连贯性、综合评分）
    返回：Gemini生成的中文报告（180字以内）
    """
    # 解包评分
    (global_s, smooth_s, stability_s, continuity_s, total_s) = scores

    # 构建Gemini提示词（Prompt Engineering：明确任务、格式、约束）
    prompt = f"""
以下是一个俯卧撑动作的多维度质量分析成绩：

- 综合评分：{total_s:.2f}
- 全局标准度（姿态是否接近标准）：{global_s:.2f}
- 动作平滑度（是否顺畅、是否有多余抖动）：{smooth_s:.2f}
- 局部稳定性（小片段动作的波动情况）：{stability_s:.2f}
- 动作连贯性（整体是否流畅、发力是否一致）：{continuity_s:.2f}

请作为“专业私教 + 人体运动学专家”，基于以上分数输出一份具有指导意义的专业动作分析报告（180 字以内）。

报告必须包含以下内容：

---

【1. 动作阶段解读】  
将俯卧撑动作分为 4 个阶段：① 下降阶段 ② 底部稳定 ③ 推起阶段 ④ 顶部锁定稳定  
结合评分判断用户在哪些阶段表现最好、哪些阶段最弱，并说明原因。

---

【2. 强项 vs 弱项对比分析】  
根据四个维度的分数差异，说明：
- 用户动作最大的优势是什么？（例如：姿态好、发力均匀）
- 最核心的弱点是什么？（例如：核心不稳、手肘路径偏移）
注意：必须指出“最关键问题”，而不是泛泛而谈。

---

【3. 解释问题原因】  
从运动学角度解释：
- 为什么会出现该弱点？
- 这种问题一般与哪些身体控制能力有关？（如肩胛控制、核心力量、肘关节稳定）
写得让用户能“理解错误本质”。

---

【4. 具体、可执行、动作明确的训练建议】  
提供 2～3 个具体训练动作，必须包含：
- 动作名称
- 训练目的
- 如何执行（简短步骤）
- 对俯卧撑改善的对应作用

例如可包括：3:1:3 慢节奏俯卧撑、平板支撑、肩胛前收/后缩练习、弹力带辅助俯卧撑

要求建议“可直接练”，不要写空话。

---

最终输出结构如下（不要加标题）：
1）动作整体表现
2）动作阶段解读
3）强项与弱项对比
4）问题原因分析
5）可执行的训练建议

"""

    if gemini_client is None:
        raise RuntimeError("Gemini 客户端未配置，无法生成文字报告。")

    try:
        response = gemini_client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt
        )
        return (response.text or "").strip()

    except Exception as e:
        return f"❌ Gemini 调用失败：{str(e)}"


def analyze_pushup_frames(frames, gemini_client=None):
    """对前端上传的动作帧进行综合打分，并可选生成Gemini报告。"""
    model, template = get_quality_model()
    x, length = frames_to_tensor(frames)
    scores = full_quality_analysis(model, x, length, template)

    report = None
    if gemini_client is not None:
        report = gemini_analysis(scores, gemini_client)

    return {
        "global_score": round(scores[0], 2),
        "smooth_score": round(scores[1], 2),
        "stability_score": round(scores[2], 2),
        "continuity_score": round(scores[3], 2),
        "total_score": round(scores[4], 2),
        "report": report,
    }

# ==============================================================================
# 7. 主入口（程序执行流程：加载数据 → 计算评分 → 生成报告）
# ==============================================================================
if __name__ == "__main__":
    # 测试用JSON文件路径（需替换为实际的动作数据文件）
    test_json = "666.jsonl"

    try:
        model, template = get_quality_model()
        x, length = load_action(test_json)
        print("\n=== 多维度评分计算 (v2.2) ===")
        scores = full_quality_analysis(model, x, length, template)
        print(scores)

    except Exception as e:
        # 全局异常捕获：打印执行过程中的错误信息
        print(f"\n❌ 程序执行出错：{str(e)}")
