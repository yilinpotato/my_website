
import re  # 正则表达式库，用于提取JSONL文件中的JSON对象
import json  # JSON数据解析库，读取动作帧数据
import torch  # PyTorch核心库，提供张量计算和深度学习基础
import torch.nn as nn  # PyTorch神经网络层模块（如LSTM、Conv1d）
import torch.nn.functional as F  # PyTorch神经网络函数（如激活、损失计算）
from torch.utils.data import Dataset, DataLoader  # 数据加载工具：Dataset定义数据集，DataLoader批量加载
from tqdm import tqdm  # 进度条库，可视化数据处理和训练进度

# ============================
# 配置参数（训练超参数和路径）
# ============================

# JSONL数据文件路径（存储动作帧和标签数据）
JSONL_PATH = r"C:\Users\34926\Desktop\LSTM_TCN_train\output(2).jsonl"#改为自己的路径
EPOCHS = 170  # 训练总轮数
BATCH_SIZE = 8  # 每批次样本数
LR = 1e-3  # 学习率（控制参数更新步长）
NEXT_K = 3   # 下一帧预测任务：预测动作序列的最后k帧

# ============================
# 数据集类（自定义动作数据集，与推理代码保持一致）
# 功能：读取JSONL数据，处理无效帧，转换为模型可接受的Tensor格式
# ============================

class ActionDataset(Dataset):
    def __init__(self, jsonl_path):
        super().__init__()  # 继承Dataset类的初始化方法
        self.samples = []  # 存储处理后的样本：每个样本是(动作序列Tensor, 标签Tensor)

        # 打开JSONL文件，读取全部内容（UTF-8编码避免中文乱码）
        with open(jsonl_path, "r", encoding="utf-8") as f:
            text = f.read().strip()

        # 兼容两种JSON格式：1. 数组包裹的JSON（[{}, {}]） 2. 逐行JSON（JSONL）
        if text.startswith("[") and text.endswith("]"):
            text = text[1:-1].strip()  # 去除首尾的[]，转为逐对象格式

        # 正则表达式提取所有JSON对象（匹配{}包裹的内容，忽略换行）
        objects = re.findall(r"\{.*?\}", text, flags=re.S)
        print(f"✔ 检测到 {len(objects)} 个 JSON 对象，开始处理...")

        # 遍历每个JSON对象，解析并处理数据（tqdm显示进度条）
        for obj_str in tqdm(objects, desc="解析 JSON"):
            try:
                o = json.loads(obj_str)  # 解析单个JSON对象
            except:
                print("解析错误：", obj_str[:200])  # 打印错误对象前200字符，便于调试
                continue  # 跳过解析失败的对象

            # 提取动作帧数据，若没有frames字段则跳过
            raw_frames = o.get("frames", [])
            if not isinstance(raw_frames, list) or len(raw_frames) == 0:
                continue  # 跳过无有效帧的样本

            clean_frames = []  # 存储处理后的有效帧（每个帧99维）
            for frame in raw_frames:
                # 处理空帧/非列表帧：替换为33个None（对应33个关键点）
                if frame is None or not isinstance(frame, list):
                    frame = [None] * 33

                # 关键点数量校准：不足33个则补None，超过33个则截断
                if len(frame) < 33:
                    frame = frame + [None] * (33 - len(frame))
                elif len(frame) > 33:
                    frame = frame[:33]

                clean_frame = []  # 存储单个帧的扁平化特征（33个关键点×3坐标=99维）
                for pt in frame:
                    # 校验关键点格式：必须是长度为3的列表，且元素为数字（x,y,z坐标）
                    if (
                        isinstance(pt, list) and len(pt) == 3 and
                        all(isinstance(v, (float, int)) for v in pt)
                    ):
                        clean_frame.append(pt)  # 保留有效关键点
                    else:
                        clean_frame.append([0.0, 0.0, 0.0])  # 无效关键点填充(0,0,0)
                clean_frames.append(clean_frame)  # 添加处理后的单帧

            # 转换为Tensor：[帧数量T, 33个关键点, 3坐标] → 重塑为[ T, 99 ]（扁平化）
            frames = torch.tensor(clean_frames, dtype=torch.float32)  # [T, 33, 3]
            T, _, _ = frames.shape
            frames = frames.reshape(T, 99)  # [T, 99]：每个帧99维特征

            # 处理标签："good"转为1.0（好动作），其他转为0.0（坏动作）
            label = 1.0 if str(o.get("label", "")).lower() == "good" else 0.0
            self.samples.append((frames, torch.tensor(label, dtype=torch.float32)))  # 添加样本到列表

        # 打印数据集加载结果
        print(f"✔ 成功加载 {len(self.samples)} 条样本")

    def __len__(self):
        return len(self.samples)  # 数据集长度：样本数量

    def __getitem__(self, i):
        return self.samples[i]  # 按索引获取样本：(动作序列Tensor[ T,99 ], 标签Tensor[1])


def pad_collate_fn(batch):
    """
    DataLoader的批量处理函数：对变长序列做padding，统一批次维度
    参数：batch - 列表，每个元素是(动作序列[Ti,99], 标签[1])
    返回：padded[B,T_max,99] - 填充后的批量序列，lengths[B] - 每个序列的原始长度，labels[B] - 批量标签
    """
    seqs, labels = zip(*batch)              # 解压batch：seqs是序列列表，labels是标签列表
    lengths = torch.tensor([len(s) for s in seqs], dtype=torch.long)  # 记录每个序列的原始长度
    # 对序列做padding（填充0），统一为批次最大长度T_max，输出[B, T_max, 99]
    padded = nn.utils.rnn.pad_sequence(seqs, batch_first=True)
    labels = torch.tensor(labels, dtype=torch.float32)           # 转换标签为Tensor[B]
    return padded, lengths, labels  # 返回批量数据：序列、长度、标签


# ============================
# 模型组件（LSTM编码器、TCN编码器、解码器、预测器、分类器、中心损失）
# 核心设计：LSTM提取时序特征 + TCN提取局部结构特征 → 融合后多任务训练
# ============================

class LSTMEncoder(nn.Module):
    """LSTM编码器：处理变长动作序列，提取时序依赖特征（如动作先后顺序、节奏）"""
    def __init__(self, input_dim=99, hidden=128, embed_dim=256):
        super().__init__()  # 继承nn.Module初始化
        # 双向LSTM层：input_dim=99（帧特征维度），hidden=128（隐藏层维度），2层堆叠
        # bidirectional=True：双向LSTM（前后向各1层），batch_first=True：输入格式[B,T,input_dim]
        # dropout=0.3：隐藏层输出dropout，防止过拟合
        self.lstm = nn.LSTM(input_dim, hidden, 2,
                            bidirectional=True, batch_first=True, dropout=0.3)
        # 全连接层：将双向LSTM输出（hidden×2=256维）映射到目标嵌入维度256
        self.fc = nn.Linear(hidden * 2, embed_dim)

    def forward(self, x, lengths):
        """
        前向传播：输入变长序列，输出时序特征嵌入
        参数：x[B,T,99] - 批量动作序列，lengths[B] - 每个序列的原始长度
        返回：[B,256] - LSTM时序特征嵌入
        """
        lengths_sorted, idx = torch.sort(lengths, descending=True)  # 序列长度降序排序，获取排序索引
        x = x[idx]  # 按长度排序重新排列批量序列（适配pack_padded_sequence）

        # 打包变长序列：忽略padding部分，提升计算效率（enforce_sorted=True确保输入已排序）
        packed = nn.utils.rnn.pack_padded_sequence(
            x, lengths_sorted.cpu(), batch_first=True, enforce_sorted=True
        )
        out, _ = self.lstm(packed)  # LSTM前向传播：out是打包后的特征
        # 解包序列：恢复为padding后的张量格式[B, T_max, 2*hidden]（2*hidden=256）
        out, _ = nn.utils.rnn.pad_packed_sequence(out, batch_first=True)

        B = out.size(0)  # 获取批量大小B
        # 构造最后一个有效时间步的索引：(长度-1) → 扩展为[B,1,2*hidden]适配gather
        last = (lengths_sorted - 1).view(B, 1, 1).expand(B, 1, out.size(2))
        feat = out.gather(1, last).squeeze(1)  # 提取每个序列的最后有效帧特征[B,2*hidden]

        _, inv = idx.sort()  # 获取排序索引的逆序（用于恢复原始批量顺序）
        feat = feat[inv]  # 恢复原始批量顺序
        return self.fc(feat)  # 全连接层映射，输出[B,256]时序嵌入


class TCNEncoder(nn.Module):
    """TCN编码器：提取动作的局部结构特征（如关节角度、身体姿态的空间关系）"""
    def __init__(self, input_dim=99, channels=[128, 128, 128], embed_dim=256):
        super().__init__()  # 继承nn.Module初始化
        layers = []  # 存储TCN的卷积层和激活层
        in_ch = input_dim  # 初始输入通道数=帧特征维度99
        # 构建3层1D卷积（捕捉局部时序结构）
        for out_ch in channels:
            # 1D卷积：kernel_size=3（感受野覆盖3个连续帧），padding=1（保持序列长度不变）
            layers.append(nn.Conv1d(in_ch, out_ch, kernel_size=3, padding=1))
            layers.append(nn.ReLU())  # ReLU激活函数，引入非线性
            in_ch = out_ch  # 更新输入通道数为当前输出通道数
        self.net = nn.Sequential(*layers)  # 组合层为序列模型（TCN主体）
        # 全连接层：将TCN输出的通道特征（128维）映射到目标嵌入维度256
        self.fc = nn.Linear(channels[-1], embed_dim)

    def forward(self, x, lengths=None):
        """
        前向传播：输入动作序列，输出局部结构特征嵌入
        参数：x[B,T,99] - 批量动作序列，lengths（可选）- 序列长度（TCN无需排序，仅兼容接口）
        返回：[B,256] - TCN局部结构特征嵌入
        """
        x = x.transpose(1, 2)  # 维度转换：[B,T,99] → [B,99,T]（适配1D卷积输入格式[B,通道,T]）
        out = self.net(x)      # TCN前向传播：输出[B,128,T]（128是最后一层卷积通道数）
        feat = out.mean(dim=2) # 时间维度平均池化：[B,128,T] → [B,128]（全局局部特征）
        return self.fc(feat)   # 全连接层映射，输出[B,256]局部结构嵌入


# -------- v2.0 重构解码器：Teacher Forcing 版本 --------
class ReconstructionDecoder(nn.Module):
    """
    重构解码器：从融合嵌入重构原始动作序列（评估动作平滑度）
    采用Teacher Forcing：解码时输入真实序列，加速训练收敛
    损失计算时屏蔽padding区域，确保只计算有效帧
    """
    def __init__(self, embed_dim=512, pose_dim=99, hidden=128, num_layers=1):
        super().__init__()  # 继承nn.Module初始化
        # 线性层：将融合嵌入（512维）映射到LSTM初始隐藏态h0
        self.init_h = nn.Linear(embed_dim, hidden)
        # 线性层：将融合嵌入（512维）映射到LSTM初始细胞态c0
        self.init_c = nn.Linear(embed_dim, hidden)
        # LSTM解码器：输入=动作帧特征（99维），隐藏层=128，1层，batch_first=True
        self.lstm = nn.LSTM(pose_dim, hidden, num_layers=num_layers, batch_first=True)
        # 全连接层：将LSTM输出（128维）映射回原始动作帧维度（99维）
        self.fc = nn.Linear(hidden, pose_dim)

    def forward(self, embed, x, lengths):
        """
        前向传播：输入融合嵌入和原始序列，输出重构序列
        参数：embed[B,512] - 融合特征嵌入，x[B,T_max,99] - 原始动作序列（已padding），lengths[B] - 序列长度
        返回：recon[B,T_max,99] - 重构的动作序列
        """
        B, T_max, _ = x.shape  # B=批量大小，T_max=批次最大序列长度

        # 初始化LSTM隐藏态h0和细胞态c0：[B,512] → [B,128] → 扩展为[1,B,128]（LSTM输入格式）
        h0 = self.init_h(embed).unsqueeze(0)
        c0 = self.init_c(embed).unsqueeze(0)

        # LSTM前向传播：输入原始序列x，初始状态(h0,c0)，输出[B,T_max,128]
        out, _ = self.lstm(x, (h0, c0))
        recon = self.fc(out)  # 全连接层映射：[B,T_max,128] → [B,T_max,99]（重构序列）
        return recon


# -------- v2.0 下一帧预测器：预测最后k帧 --------
class NextFramePredictor(nn.Module):
    """
    下一帧预测器：从全局融合嵌入预测动作序列的最后k帧
    评估动作的合理性和连贯性（合理动作的后续帧可被准确预测）
    """
    def __init__(self, embed_dim=512, output_dim=99, k=3):
        super().__init__()  # 继承nn.Module初始化
        self.k = k  # 预测的帧数
        # 全连接网络：从融合嵌入（512维）映射到k帧动作特征（99×k维）
        self.fc = nn.Sequential(
            nn.Linear(embed_dim, embed_dim),  # 512→512线性变换
            nn.ReLU(),  # ReLU激活，引入非线性
            nn.Linear(embed_dim, output_dim * k)  # 512→99×k（输出k帧的扁平化特征）
        )

    def forward(self, embed):
        """
        前向传播：输入融合嵌入，输出预测的k帧动作
        参数：embed[B,512] - 融合特征嵌入
        返回：[B,k,99] - 预测的k帧动作序列
        """
        B = embed.size(0)  # 批量大小B
        out = self.fc(embed)  # 全连接网络输出：[B, 99×k]
        return out.view(B, self.k, -1)  # 重塑维度：[B,99×k] → [B,k,99]（k帧动作）


class GoodBadClassifier(nn.Module):
    """动作质量二分类器：从融合嵌入判断动作是"好"（1.0）还是"坏"（0.0）"""
    def __init__(self, embed_dim=512):
        super().__init__()  # 继承nn.Module初始化
        # 全连接网络：512维融合嵌入 → 128维 → 1维概率（Sigmoid激活）
        self.fc = nn.Sequential(
            nn.Linear(embed_dim, 128),  # 512→128线性变换
            nn.ReLU(),  # ReLU激活，引入非线性
            nn.Linear(128, 1),  # 128→1线性变换（二分类输出）
            nn.Sigmoid()  # Sigmoid激活，输出0-1之间的概率
        )

    def forward(self, embed):
        # 前向传播：输入[B,512] → 输出[B]（压缩最后一维，每个样本一个概率值）
        return self.fc(embed).squeeze(-1)


# -------- v2.0 中心损失：让good/bad动作在嵌入空间更分离 --------
class CenterLoss(nn.Module):
    """
    中心损失：最小化样本嵌入与对应类别中心的距离
    作用：让同一类（good/bad）的嵌入更集中，不同类的嵌入更分离
    公式：L = mean(|| f(x) - c_y ||²)，f(x)是样本嵌入，c_y是x所属类别的中心
    """
    def __init__(self, num_classes=2, feat_dim=512):
        super().__init__()  # 继承nn.Module初始化
        # 类别中心参数：可学习的Tensor，形状[2,512]（2类，每类512维中心）
        self.centers = nn.Parameter(torch.randn(num_classes, feat_dim))

    def forward(self, features, labels):
        """
        前向传播：计算中心损失
        参数：features[B,512] - 样本融合嵌入，labels[B] - 样本标签（0.0/1.0）
        返回：center_loss - 中心损失值（标量）
        """
        # 标签转换：float→long，确保索引在类别范围内（0/1）
        labels_long = labels.long().clamp(min=0, max=self.centers.size(0)-1)
        centers_batch = self.centers[labels_long]  # 按标签取对应类别的中心：[B,512]
        # 计算每个样本嵌入与类别中心的平方距离，求均值作为损失
        loss = (features - centers_batch).pow(2).sum(dim=1).mean()
        return loss


# ============================
# 融合质量模型（v2.0）：整合所有组件，实现多任务训练
# ============================

class FusionQualityModel(nn.Module):
    def __init__(self, input_dim=99, next_k=3):
        super().__init__()  # 继承nn.Module初始化
        self.lstm_encoder = LSTMEncoder(input_dim)  # LSTM时序编码器（输入99维）
        self.tcn_encoder  = TCNEncoder(input_dim)   # TCN局部结构编码器（输入99维）
        self.fusion_dim   = 512  # 融合特征维度（256+256）

        # 多任务组件
        self.decoder      = ReconstructionDecoder(self.fusion_dim, pose_dim=input_dim)  # 重构解码器
        self.next_frame   = NextFramePredictor(self.fusion_dim, output_dim=input_dim, k=next_k)  # 下一帧预测器
        self.classifier   = GoodBadClassifier(self.fusion_dim)  # 二分类器

    def encode(self, x, lengths):
        """
        编码函数：输入动作序列，输出融合特征嵌入
        参数：x[B,T,99] - 批量动作序列，lengths[B] - 序列长度
        返回：embed[B,512] - LSTM+TCN融合特征
        """
        lstm_feat = self.lstm_encoder(x, lengths)   # LSTM时序特征：[B,256]
        tcn_feat  = self.tcn_encoder(x, lengths)    # TCN局部结构特征：[B,256]
        embed = torch.cat([lstm_feat, tcn_feat], dim=1)  # 特征拼接：[B,256+256=512]
        return embed

    def forward(self, x, lengths):
        """
        训练主路径：输入动作序列，输出融合嵌入和分类概率
        参数：x[B,T,99] - 批量动作序列，lengths[B] - 序列长度
        返回：embed[B,512] - 融合特征，cls[B] - 动作质量分类概率（0-1）
        """
        embed = self.encode(x, lengths)        # 编码得到融合特征：[B,512]
        cls   = self.classifier(embed)         # 分类预测：[B]
        return embed, cls


# ============================
# 训练工具函数（v2.0）：损失计算、单轮训练
# ============================

def reconstruction_loss(recon, x, lengths):
    """
    重构损失：仅计算有效帧的MSE损失（屏蔽padding区域）
    参数：recon[B,T_max,99] - 重构序列，x[B,T_max,99] - 原始序列，lengths[B] - 序列长度
    返回：recon_loss - 有效帧的MSE损失（标量）
    """
    B, T_max, _ = x.shape  # B=批量大小，T_max=最大序列长度
    device = x.device  # 获取数据所在设备（CPU/GPU）
    # 生成mask：标记有效帧（True）和padding帧（False）→ [B,T_max]
    mask = torch.arange(T_max, device=device)[None, :].expand(B, T_max) < lengths[:, None]
    mask = mask.unsqueeze(-1)   # 扩展维度：[B,T_max] → [B,T_max,1]（适配99维特征）

    diff_sq = (recon - x).pow(2) * mask    # 计算平方误差，屏蔽padding区域：[B,T_max,99]
    denom = mask.sum().clamp(min=1).float()  # 有效元素总数（避免除以0）
    loss = diff_sq.sum() / denom  # 有效元素的MSE损失
    return loss


def next_frame_loss(pred_seq, x, lengths, k):
    """
    下一帧预测损失：预测序列最后k帧与真实最后k帧的MSE
    处理短序列：若序列长度<T<=k，预测最后T帧
    参数：pred_seq[B,k,99] - 预测的k帧，x[B,T_max,99] - 原始序列，lengths[B] - 序列长度，k - 预设预测帧数
    返回：next_loss - 下一帧预测损失（标量）
    """
    B = x.size(0)  # 批量大小
    losses = []  # 存储每个样本的预测损失
    for i in range(B):
        T = lengths[i].item()  # 第i个样本的原始长度
        if T <= 0:
            continue  # 跳过无效样本
        k_use = min(k, T)  # 实际预测帧数（短序列取T，长序列取k）
        target = x[i, T-k_use:T, :]  # 真实目标帧：最后k_use帧 → [k_use,99]
        pred_i = pred_seq[i, -k_use:, :]  # 预测帧：取最后k_use帧 → [k_use,99]
        losses.append(F.mse_loss(pred_i, target))  # 计算单个样本的MSE损失
    if not losses:
        return torch.tensor(0.0, device=x.device)  # 无有效样本时返回0损失
    return torch.stack(losses).mean()  # 批量样本损失均值


def train_epoch(model, loader, opt, center_loss_fn, device, epoch_idx=None, k=3):
    """
    单轮训练：遍历数据集一次，更新模型参数
    参数：model - 融合质量模型，loader - 数据加载器，opt - 优化器，center_loss_fn - 中心损失函数
          device - 训练设备，epoch_idx - 当前轮次索引，k - 下一帧预测帧数
    返回：stats - 各损失的均值字典
    """
    model.train()  # 模型设为训练模式（启用dropout、BatchNorm更新）
    bce = nn.BCELoss()  # 二分类交叉熵损失（用于动作质量分类）

    # 损失统计字典：记录各损失的总和
    total = {"recon": 0.0, "next": 0.0, "cls": 0.0, "center": 0.0}

    # 遍历数据加载器的每个批次（tqdm显示训练进度）
    for x, lengths, labels in tqdm(loader, desc=f"Epoch {epoch_idx}"):
        x = x.to(device)          # 动作序列移到设备：[B,T_max,99]
        lengths = lengths.to(device)  # 序列长度移到设备：[B]
        labels = labels.to(device)  # 标签移到设备：[B]（0.0/1.0）

        # 模型前向传播：得到融合嵌入和分类概率
        embed, cls_prob = model(x, lengths)  # embed[B,512], cls_prob[B]

        # 1. 重构损失（Teacher Forcing）
        recon = model.decoder(embed, x, lengths)  # 解码器输出重构序列：[B,T_max,99]
        recon_l = reconstruction_loss(recon, x, lengths)  # 有效帧MSE损失

        # 2. 下一帧预测损失
        pred_seq = model.next_frame(embed)  # 预测最后k帧：[B,k,99]
        next_l = next_frame_loss(pred_seq, x, lengths, k)  # 预测损失

        # 3. 分类损失（动作质量二分类）
        cls_l = bce(cls_prob, labels)  # BCE损失：分类概率vs真实标签

        # 4. 中心损失（嵌入空间类内紧凑、类间分离）
        center_l = center_loss_fn(embed, labels)

        # 5. 总损失：加权求和（可根据训练效果微调权重）
        loss = (
            0.8 * recon_l +    # 重构损失权重0.8
            0.4 * next_l +     # 下一帧预测损失权重0.4
            1.5 * cls_l +      # 分类损失权重1.5（重点优化）
            0.1 * center_l     # 中心损失权重0.1
        )

        # 反向传播与参数更新
        opt.zero_grad()  # 清空梯度（避免梯度累积）
        loss.backward()  # 计算总损失的梯度
        opt.step()       # 优化器更新模型参数和中心损失的类别中心

        # 累积各损失值
        total["recon"]  += recon_l.item()
        total["next"]   += next_l.item()
        total["cls"]    += cls_l.item()
        total["center"] += center_l.item()

    n = len(loader)  # 批次数量
    return {k: v / n for k, v in total.items()}  # 返回各损失的均值


# ============================
# 构建Good动作模板（与推理代码逻辑一致）
# 功能：计算所有good动作嵌入的均值，作为标准动作模板
# ============================

def build_good_template(model, loader, device):
    """
    构建标准动作模板：所有good样本的融合嵌入均值
    参数：model - 训练好的模型，loader - 数据加载器，device - 设备
    返回：good_template[1,512] - 标准动作的融合嵌入模板
    """
    model.eval()  # 模型设为评估模式（禁用dropout、BatchNorm固定）
    goods = []  # 存储所有good样本的嵌入
    with torch.no_grad():  # 禁用梯度计算（节省内存，加速推理）
        for x, lengths, labels in loader:
            x = x.to(device)  # 序列移到设备
            lengths = lengths.to(device)  # 长度移到设备
            labels = labels.to(device)  # 标签移到设备
            embed, _ = model(x, lengths)  # 编码得到嵌入：[B,512]
            # 筛选good样本（标签>0.5），收集其嵌入
            for e, lab in zip(embed, labels):
                if lab > 0.5:
                    goods.append(e.cpu())  # 移到CPU并存储
    # 处理无good样本的极端情况：使用所有样本的嵌入均值
    if len(goods) == 0:
        print("⚠ 警告：没有 good 样本，good_template 将使用全体均值")
        with torch.no_grad():
            all_embeds = []
            for x, lengths, _ in loader:
                x = x.to(device)
                lengths = lengths.to(device)
                emb, _ = model(x, lengths)
                all_embeds.append(emb.cpu())
            all_embeds = torch.cat(all_embeds, dim=0)  # 所有样本嵌入：[N,512]
            return all_embeds.mean(0, keepdim=True)  # 全体均值：[1,512]
    # good样本嵌入均值：[1,512]（标准动作模板）
    return torch.stack(goods).mean(0, keepdim=True)


# ============================
# 主函数：训练流程入口
# ============================

def main():
    # 自动选择训练设备：优先GPU（cuda），无GPU则用CPU
    device = "cuda" if torch.cuda.is_available() else "cpu"

    # 1. 初始化数据集和数据加载器
    dataset = ActionDataset(JSONL_PATH)  # 加载动作数据集
    loader = DataLoader(dataset, batch_size=BATCH_SIZE,
                        shuffle=True, collate_fn=pad_collate_fn)  # 批量加载（shuffle打乱数据）

    # 2. 初始化模型和损失函数
    model = FusionQualityModel(input_dim=99, next_k=NEXT_K).to(device)  # 融合模型移到设备
    # 中心损失函数（2类：good/bad，嵌入维度512），移到设备
    center_loss_fn = CenterLoss(num_classes=2, feat_dim=model.fusion_dim).to(device)

    # 3. 初始化优化器：优化模型参数 + 中心损失的类别中心参数
    opt = torch.optim.Adam(
        list(model.parameters()) + list(center_loss_fn.parameters()),
        lr=LR  # 学习率
    )

    print("开始训练...")

    # 4. 训练循环：遍历EPOCHS轮
    for ep in range(1, EPOCHS + 1):
        # 单轮训练：返回各损失均值
        stats = train_epoch(model, loader, opt, center_loss_fn, device, ep, k=NEXT_K)
        # 打印当前轮次的损失统计
        print(f"[{ep}] recon={stats['recon']:.4f}, "
              f"next={stats['next']:.4f}, "
              f"cls={stats['cls']:.4f}, "
              f"center={stats['center']:.4f}")

    # 5. 训练结束：构建标准动作模板
    good_template = build_good_template(model, loader, device)

    # 6. 保存模型和模板：模型权重 + good动作模板
    torch.save({
        "model": model.state_dict(),  # 模型参数
        "good_template": good_template  # 标准动作模板
    }, "quality_model.pth")

    print("模型已保存到 quality_model.pth")


# 程序入口：执行main函数
if __name__ == "__main__":
    main()



