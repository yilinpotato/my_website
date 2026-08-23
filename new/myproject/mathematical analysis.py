import math
import logging
import json
import os
from enum import Enum
from typing import List, Dict, Optional, Tuple, Any
from dataclasses import dataclass

# 设置日志格式，方便调试查看每一步的计算过程
logging.basicConfig(level=logging.INFO, format='%(asctime)s - [MathModel] - %(message)s')
logger = logging.getLogger(__name__)


# ==========================================
# 1. 配置与常量定义 (Configuration)
# 将文档中的所有数字提取到这里，方便后续微调
# ==========================================
class Config:
    """存储所有算法阈值和评分权重"""

    # --- 评分权重 (Total 100) ---
    WEIGHTS = {
        "S_D_Elbow": 30,  # 动作深度-肘部角度
        "S_D_Ratio": 15,  # 动作深度-下沉比率
        "C_Lock": 5,  # 顶峰锁定
        "S_C": 40,  # 核心稳定性
        "S_S_Base": 10  # 安全规范性基础分
    }

    # --- 阈值标准 (Thresholds) ---
    # 1. 肘部角度 (Elbow Angle)
    TH_ELBOW_STANDARD_MIN = 80  # 标准区间下限
    TH_ELBOW_STANDARD_MAX = 100  # 标准区间上限
    TH_ELBOW_LOCK = 170  # 顶峰锁定角度
    TH_ELBOW_DANGEROUS = 75  # [安全阈值] 肘部压力过大

    # 2. 下沉比率 (Descent Ratio)
    TH_RATIO_STANDARD_MIN = 0.30
    TH_RATIO_STANDARD_MAX = 0.45
    TH_RATIO_DANGEROUS = 0.25  # [安全阈值] 过度下沉

    # 3. 身体直线度 (Body Linearity)
    TH_BODY_PERFECT_DIFF = 5  # 完美偏差 <= 5度
    TH_BODY_ACCEPTABLE_DIFF = 10  # 可接受偏差 <= 10度
    TH_BODY_COLLAPSE = 170  # [安全阈值] 塌腰
    TH_BODY_PIKE = 190  # [安全阈值] 撅臀

    # 4. 判定参数
    PASSING_SCORE = 80  # 二分类的及格线
    STATE_DESCENT_TRIGGER = 160  # 肘角小于此值开始判定为下落


# ==========================================
# 2. 数据结构定义 (Data Structures)
# ==========================================
@dataclass
class Point:
    """定义关键点坐标"""
    x: float
    y: float
    visibility: float = 1.0  # 置信度，预留给后续过滤低质量数据


class FrameData:
    """封装单帧的所有关键数据"""

    def __init__(self, frame_index: int, landmarks: Dict[str, Point]):
        self.frame_index = frame_index
        self.landmarks = landmarks
        self.issues = []  # 存储该帧检测到的具体问题（如：塌腰）

    def get_point(self, name: str) -> Optional[Point]:
        return self.landmarks.get(name)


# ==========================================
# 3. 几何计算工具类 (Geometry Utils)
# ==========================================
class GeometryUtils:
    @staticmethod
    def calculate_angle(a: Point, b: Point, c: Point) -> float:
        """计算三点夹角 (b为顶点), 返回 0-180 度"""
        if not (a and b and c):
            return 180.0

        # 使用 atan2 计算向量夹角
        ang = math.degrees(
            math.atan2(a.y - b.y, a.x - b.x) -
            math.atan2(c.y - b.y, c.x - b.x)
        )
        return abs(ang) if abs(ang) <= 180 else 360 - abs(ang)

    @staticmethod
    def calculate_distance(p1: Point, p2: Point) -> float:
        """计算两点欧几里得距离"""
        if not (p1 and p2): return 0.0
        return math.sqrt((p1.x - p2.x) ** 2 + (p1.y - p2.y) ** 2)

    @staticmethod
    def calculate_vertical_distance(p_target: Point, p_ref: Point) -> float:
        """计算垂直距离 (y轴差值绝对值)"""
        if not (p_target and p_ref): return 0.0
        return abs(p_target.y - p_ref.y)


# ==========================================
# 4. 动作状态机 (Action State Machine)
# ==========================================
class PushUpState(Enum):
    PREPARE = "Prepare"  # 准备/支撑
    DESCENT = "Descent"  # 下降
    ASCENT = "Ascent"  # 上升
    COMPLETE = "Complete"  # 完成一次


class ActionStateMachine:
    """
    通过肘部角度变化，识别动作当前处于什么阶段。
    避免对无效帧（如人还没趴下时）进行评分。
    """

    def __init__(self):
        self.current_state = PushUpState.PREPARE
        self.min_angle = 180.0
        self.max_angle = 0.0

    def update(self, frame_idx: int, elbow_angle: float):
        # 简单的状态流转逻辑
        if self.current_state == PushUpState.PREPARE:
            if elbow_angle < Config.STATE_DESCENT_TRIGGER:
                self.current_state = PushUpState.DESCENT
                # logger.debug(f"Frame {frame_idx}: 开始下降")

        elif self.current_state == PushUpState.DESCENT:
            # 持续寻找最低点
            if elbow_angle < self.min_angle:
                self.min_angle = elbow_angle

            # 如果角度显著回升，切换到上升状态
            if elbow_angle > self.min_angle + 15:
                self.current_state = PushUpState.ASCENT
                # logger.debug(f"Frame {frame_idx}: 到底回升 (最低点: {self.min_angle:.1f})")

        elif self.current_state == PushUpState.ASCENT:
            if elbow_angle > self.max_angle:
                self.max_angle = elbow_angle

            # 接近锁定，认为动作完成
            if elbow_angle >= Config.TH_ELBOW_LOCK - 10:
                self.current_state = PushUpState.COMPLETE


# ==========================================
# 5. 核心评估器 (Evaluator)
# ==========================================
class PushUpEvaluator:
    def __init__(self):
        self.frames: List[FrameData] = []
        self.state_machine = ActionStateMachine()

        # 记录整个动作过程中的极值数据
        self.stats = {
            "elbow_min": 180.0,  # 最低点肘角
            "elbow_max": 0.0,  # 最高点肘角
            "body_worst_diff": 0.0,  # 身体最不直的偏差度
            "ratio_min": 1.0,  # 最深的下沉比率
            "has_severe_error": False,
            "error_logs": []  # 记录具体的错误信息
        }

    def load_frame_data(self, raw_frames_list: List[Dict]):
        """加载外部关键点数据"""
        self.frames = []
        for idx, raw_data in enumerate(raw_frames_list):
            landmarks = {}
            for k, v in raw_data.items():
                landmarks[k] = Point(x=v[0], y=v[1])
            self.frames.append(FrameData(idx, landmarks))

    def analyze(self) -> Dict:
        """执行完整分析流程"""
        if not self.frames:
            return {"error": "No data"}

        # 1. 遍历每一帧，计算指标并更新极值
        for frame in self.frames:
            self._process_single_frame(frame)

        # 2. 根据统计的极值进行打分
        score_details = self._calculate_final_score()

        # 3. 生成最终的二分类结论 (Good/Bad)
        classification = self._classify_result(score_details)

        return {
            "classification": classification,
            "total_score": score_details["Total"],
            "score_details": score_details,
            "metrics_summary": {
                "min_elbow_angle": self.stats["elbow_min"],
                "worst_body_diff": self.stats["body_worst_diff"],
                "min_descent_ratio": self.stats["ratio_min"]
            },
            "errors": self.stats["error_logs"]
        }

    def _process_single_frame(self, frame: FrameData):
        """单帧计算逻辑"""
        # 获取关键点
        p_w, p_e, p_s = frame.get_point('wrist'), frame.get_point('elbow'), frame.get_point('shoulder')
        p_h, p_k = frame.get_point('hip'), frame.get_point('knee')

        # 计算基础角度
        theta_elbow = GeometryUtils.calculate_angle(p_w, p_e, p_s)
        theta_body = GeometryUtils.calculate_angle(p_s, p_h, p_k)

        # 计算下沉比率 (仅当数据有效时)
        ratio = 1.0
        l_arm = GeometryUtils.calculate_distance(p_s, p_w)
        if l_arm > 0:
            mid_x = (p_s.x + p_e.x) / 2
            mid_y = (p_s.y + p_e.y) / 2
            p_mid = Point(mid_x, mid_y)
            d = GeometryUtils.calculate_vertical_distance(p_mid, p_w)
            ratio = d / l_arm

        # --- 实时安全监控 (Real-time Safety Check) ---
        # 1. 肘部压力检查
        if theta_elbow < Config.TH_ELBOW_DANGEROUS:
            msg = f"Frame {frame.frame_index}: 危险! 肘部角度过小 ({theta_elbow:.1f}°)"
            if msg not in self.stats["error_logs"]:  # 避免重复刷屏
                self.stats["error_logs"].append(msg)
                self.stats["has_severe_error"] = True

        # 2. 塌腰检查
        if theta_body < Config.TH_BODY_COLLAPSE:
            msg = f"Frame {frame.frame_index}: 严重塌腰 ({theta_body:.1f}°)"
            self.stats["error_logs"].append(msg)
            self.stats["has_severe_error"] = True

        # --- 更新全局统计极值 ---
        self.stats["elbow_min"] = min(self.stats["elbow_min"], theta_elbow)
        self.stats["elbow_max"] = max(self.stats["elbow_max"], theta_elbow)
        self.stats["ratio_min"] = min(self.stats["ratio_min"], ratio)

        # 记录身体偏离直线的最大程度
        diff = abs(theta_body - 180)
        self.stats["body_worst_diff"] = max(self.stats["body_worst_diff"], diff)

        # 更新状态机
        self.state_machine.update(frame.frame_index, theta_elbow)

    def _calculate_final_score(self) -> Dict:
        """根据文档公式计算各项得分"""
        scores = {}

        # 1. [动作深度] 肘部角度得分 (30分)
        theta = self.stats["elbow_min"]
        if Config.TH_ELBOW_STANDARD_MIN <= theta <= Config.TH_ELBOW_STANDARD_MAX:
            scores["S_D_Elbow"] = 30
        elif theta > Config.TH_ELBOW_STANDARD_MAX:
            scores["S_D_Elbow"] = max(0, 30 - 3 * (theta - Config.TH_ELBOW_STANDARD_MAX))
        else:
            scores["S_D_Elbow"] = 0

            # 2. [动作深度] 下沉比率得分 (15分)
        r_min = self.stats["ratio_min"]
        if Config.TH_RATIO_STANDARD_MIN <= r_min <= Config.TH_RATIO_STANDARD_MAX:
            scores["S_D_Ratio"] = 15
        elif r_min > Config.TH_RATIO_STANDARD_MAX:
            scores["S_D_Ratio"] = max(0, 15 - 100 * (r_min - Config.TH_RATIO_STANDARD_MAX))
        else:
            scores["S_D_Ratio"] = 0

        # 3. [顶峰锁定] (5分)
        if self.stats["elbow_max"] >= Config.TH_ELBOW_LOCK:
            scores["C_Lock"] = 5
        else:
            scores["C_Lock"] = 0

        # 4. [核心稳定性] (40分)
        delta = self.stats["body_worst_diff"]
        if delta <= Config.TH_BODY_PERFECT_DIFF:
            scores["S_C"] = 40
        elif delta <= Config.TH_BODY_ACCEPTABLE_DIFF:
            scores["S_C"] = max(0, 40 - 4 * (delta - Config.TH_BODY_PERFECT_DIFF))
        else:
            scores["S_C"] = 0

        # 5. [安全惩罚] (10分起扣)
        penalty = 0
        if self.stats["has_severe_error"]:
            penalty = 10

        if r_min < Config.TH_RATIO_DANGEROUS:
            penalty = 10
            self.stats["error_logs"].append(f"全局警告: 下沉过深 Ratio={r_min:.2f}")

        scores["S_S"] = max(0, 10 - penalty)

        # 计算总分
        scores["Total"] = sum(scores.values())
        return scores

    def _classify_result(self, score_report: Dict) -> str:
        """二分类裁决: Good / Bad"""
        total = score_report["Total"]
        # 必须满足两个条件：分数及格 且 没有安全扣分
        is_safe = score_report["S_S"] > 0

        if total >= Config.PASSING_SCORE and is_safe:
            return "GOOD"
        else:
            return "BAD"


# ==========================================
# 6. 批量处理工具 (Batch Processing Tool)
# ==========================================
class BatchProcessor:
    """模拟从文件夹读取多个文件进行评估，生成报表"""

    def __init__(self, data_folder: str = "./data"):
        self.data_folder = data_folder
        self.results = []

    def run_simulation(self):
        """运行模拟测试"""
        print(f"正在启动批量评估任务...")

        # 模拟三个不同类型的测试用例
        test_cases = {
            "Case_01_Standard.mp4": self._mock_data("standard"),
            "Case_02_Collapse.mp4": self._mock_data("collapse"),
            "Case_03_Shallow.mp4": self._mock_data("shallow")
        }

        for filename, data in test_cases.items():
            evaluator = PushUpEvaluator()
            evaluator.load_frame_data(data)
            report = evaluator.analyze()

            # 整理结果用于 CSV 输出
            summary = {
                "Filename": filename,
                "Classification": report["classification"],
                "Total_Score": report["total_score"],
                "Elbow_Score": report["score_details"]["S_D_Elbow"],
                "Core_Score": report["score_details"]["S_C"],
                "Safety_Score": report["score_details"]["S_S"],
                "Error_Count": len(report["errors"])
            }
            self.results.append(summary)
            print(f"[处理完成] {filename}: {summary['Classification']} (得分: {summary['Total_Score']})")

    def export_report(self):
        """打印 CSV 格式报告"""
        print("\n" + "=" * 50)
        print("【数学模型评估报告 - 导出数据】")
        print("=" * 50)
        print("文件名, 判定结果, 总分, 肘部得分, 核心得分, 安全得分, 错误数")
        for res in self.results:
            print(f"{res['Filename']}, {res['Classification']}, {res['Total_Score']}, "
                  f"{res['Elbow_Score']}, {res['Core_Score']}, {res['Safety_Score']}, {res['Error_Count']}")
        print("=" * 50)

    def _mock_data(self, type_mode) -> List[Dict]:
        """生成模拟关键点数据 (仅用于测试代码逻辑)"""
        frames = []
        for i in range(30):
            # 基础坐标
            wrist = (100, 500)
            shoulder = (100, 100)
            hip, knee = (100, 400), (100, 800)

            # 肘部运动轨迹
            offset = i * 10 if i < 15 else (30 - i) * 10

            if type_mode == "standard":
                elbow = (150 + offset, 250 + offset)
            elif type_mode == "collapse":
                elbow = (150 + offset, 250 + offset)
                hip = (120, 400 + offset * 2)  # 模拟塌腰：Hip坐标偏移
            elif type_mode == "shallow":
                elbow = (150 + offset * 0.5, 250 + offset * 0.5)  # 模拟下不去

            frames.append({
                'wrist': wrist, 'elbow': elbow, 'shoulder': shoulder,
                'hip': hip, 'knee': knee
            })
        return frames


# ==========================================
# 7. 主程序入口
# ==========================================
if __name__ == "__main__":
    # 1. 实例化处理工具
    processor = BatchProcessor()

    # 2. 运行模拟
    processor.run_simulation()

    # 3. 导出报告 (你可以直接复制这部分输出)
    processor.export_report()

    print("\n[提示] 真实使用时，请替换 BatchProcessor 中的数据读取逻辑，接入 OpenPose/MediaPipe 的实际坐标。")