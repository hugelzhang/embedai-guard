"""
Skill: trace — 逻辑分析仪 CSV 波形解析 + Golden Trace 对比

支持: Kingst LA1010 / Saleae / 通用 CSV 导出格式
自动检测: 数字通道 / 协议解码 / 帧列表 三种格式
"""

import csv
import os
import re
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field


# ── 数据模型 ──────────────────────────────

@dataclass
class ProtocolFrame:
    """单个协议帧"""
    index: int               # 帧序号
    timestamp: float         # 时间戳 (秒)
    channel: str = ""        # 通道名 (MOSI/MISO/TX/RX...)
    data: str = ""           # 数据 (HEX 字符串如 "A5" 或 "0xA5")
    event: str = ""          # 事件 (Start/Stop/ACK/NACK)

    @property
    def data_bytes(self) -> bytes:
        """解析为字节"""
        try:
            return bytes.fromhex(self.data.replace('0x', '').replace('0X', ''))
        except ValueError:
            return b''


@dataclass
class TraceData:
    """一次采集的完整波形数据"""
    file_path: str
    format: str = ""          # 'digital' | 'protocol' | 'frame'
    channel_count: int = 0
    frames: List[ProtocolFrame] = field(default_factory=list)
    raw_samples: List[dict] = field(default_factory=list)
    sample_rate: Optional[float] = None


@dataclass
class TraceDiff:
    """两帧之间的差异"""
    index: int
    golden: ProtocolFrame
    current: ProtocolFrame
    diff_type: str           # 'missing' | 'extra' | 'data' | 'timing' | 'ok'
    detail: str = ""


@dataclass
class TraceResult:
    """Golden Trace 对比结果"""
    golden_file: str
    current_file: str
    matched: int = 0
    mismatches: List[TraceDiff] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return len(self.mismatches) == 0

    @property
    def data_errors(self) -> int:
        return sum(1 for d in self.mismatches if d.diff_type == 'data')

    @property
    def timing_warnings(self) -> int:
        return sum(1 for d in self.mismatches if d.diff_type == 'timing')

    @property
    def missing_frames(self) -> int:
        return sum(1 for d in self.mismatches if d.diff_type == 'missing')

    @property
    def extra_frames(self) -> int:
        return sum(1 for d in self.mismatches if d.diff_type == 'extra')


# ── CSV 解析 ──────────────────────────────

_COLUMN_ALIASES = {
    'time': ['time', 'time[s]', 'timestamp', 'time [s]', 'time(s)'],
    'mosi': ['mosi', 'data_mosi', 'spi_mosi'],
    'miso': ['miso', 'data_miso', 'spi_miso'],
    'tx':   ['tx', 'txd', 'uart_tx', 'data_tx'],
    'rx':   ['rx', 'rxd', 'uart_rx', 'data_rx'],
    'scl':  ['scl', 'sck', 'clk', 'sclk'],
    'sda':  ['sda', 'data'],
    'cs':   ['cs', 'nss', 'ss', 'enable'],
    'type': ['type', 'event', 'packet type'],
    'data': ['data', 'value', 'hex', 'payload'],
    'index': ['index', 'no.', 'no', 'packet id', 'frame', '#'],
}


def _normalize_header(h: str) -> str:
    """将列头标准化到类别名"""
    h = h.strip().lower().replace(' ', '_').replace('-', '_')
    for category, aliases in _COLUMN_ALIASES.items():
        if h in aliases:
            return category
    return h


def _parse_hex(val: str) -> str:
    """解析各种 HEX 格式 → 纯 HEX 字符串"""
    val = val.strip()
    if val.startswith('0x') or val.startswith('0X'):
        val = val[2:]
    # 只保留 HEX 字符
    return ''.join(c for c in val if c in '0123456789ABCDEFabcdef').upper()


def load_trace(file_path: str) -> Optional[TraceData]:
    """加载逻辑分析仪 CSV 导出文件"""
    if not os.path.exists(file_path):
        return None

    try:
        with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
            reader = csv.reader(f)
            rows = list(reader)
    except Exception:
        return None

    if len(rows) < 2:
        return None

    # 跳过元数据行（Kingst/Saleae 的前几行可能是设置信息）
    header_row = 0
    for i, row in enumerate(rows):
        if row and any(
            kw in (row[0].lower() if row else '')
            for kw in ['time', 'no.', 'index', 'frame', 'ch']
        ):
            header_row = i
            break

    headers = [_normalize_header(h) for h in rows[header_row]]
    data_rows = rows[header_row + 1:]

    trace = TraceData(file_path=file_path)

    # 检测格式
    has_time = 'time' in headers
    has_data = 'data' in headers
    has_index = 'index' in headers
    has_channels = any(h.startswith('d') or h.startswith('ch') for h in headers)

    if has_index and has_time:
        trace.format = 'frame'
    elif has_data:
        trace.format = 'protocol'
    elif has_channels:
        trace.format = 'digital'
    else:
        trace.format = 'csv'

    # 解析数据行
    for ri, row in enumerate(data_rows):
        if not row or not any(row):
            continue
        sample = {headers[i]: val for i, val in enumerate(row) if i < len(headers)}

        # 提取时间戳
        ts = 0.0
        for key in sample:
            if 'time' in key.lower() or key == 'time':
                try:
                    ts = float(sample[key])
                except ValueError:
                    ts = float(ri)  # fallback: 行号
                break

        # 构建帧
        if trace.format in ('protocol', 'frame'):
            frame_data = sample.get('data', '')
            if not frame_data:
                # 尝试从 MOSI/MISO 合并
                for ch in ['mosi', 'miso', 'tx', 'rx']:
                    if ch in sample and sample[ch]:
                        frame_data = sample[ch]
                        break

            frame = ProtocolFrame(
                index=int(sample.get('index', ri + 1)),
                timestamp=ts,
                channel=sample.get('channel', ''),
                data=_parse_hex(frame_data) if frame_data else '',
                event=sample.get('type', ''),
            )
            trace.frames.append(frame)
        elif trace.format == 'digital':
            trace.raw_samples.append(sample)

    trace.channel_count = len([h for h in headers if h not in ('time', 'index', 'type')])
    return trace


# ── Golden Trace 对比 ──────────────────────

def compare_traces(golden_path: str, current_path: str,
                   timing_tolerance_sec: float = 1e-6) -> TraceResult:
    """对比两个波形采集的差异"""
    golden = load_trace(golden_path)
    current = load_trace(current_path)

    if not golden or not current:
        result = TraceResult(golden_file=golden_path, current_file=current_path)
        if not golden:
            result.mismatches.append(TraceDiff(
                index=0, golden=ProtocolFrame(0, 0), current=ProtocolFrame(0, 0),
                diff_type='missing', detail=f"Failed to load golden: {golden_path}"))
        if not current:
            result.mismatches.append(TraceDiff(
                index=0, golden=ProtocolFrame(0, 0), current=ProtocolFrame(0, 0),
                diff_type='extra', detail=f"Failed to load current: {current_path}"))
        return result

    result = TraceResult(golden_file=golden_path, current_file=current_path)

    golden_frames = golden.frames
    current_frames = current.frames

    if not golden_frames and not current_frames:
        return result

    # 按 timestamp 对齐帧
    max_idx = max(len(golden_frames), len(current_frames))
    for i in range(max_idx):
        gf = golden_frames[i] if i < len(golden_frames) else None
        cf = current_frames[i] if i < len(current_frames) else None

        if gf and cf:
            result.matched += 1
            # 数据对比
            if gf.data and cf.data and gf.data != cf.data:
                result.mismatches.append(TraceDiff(
                    index=i,
                    golden=gf, current=cf,
                    diff_type='data',
                    detail=f"Data mismatch: golden={gf.data}, current={cf.data}"))
            # 时序对比（仅当两者都有有效时间戳时）
            elif gf.timestamp > 0 and cf.timestamp > 0:
                time_diff = abs(cf.timestamp - gf.timestamp)
                if time_diff > timing_tolerance_sec:
                    result.mismatches.append(TraceDiff(
                        index=i,
                        golden=gf, current=cf,
                        diff_type='timing',
                        detail=f"Timing offset: {time_diff*1e9:.1f}ns "
                               f"(tolerance: {timing_tolerance_sec*1e9:.0f}ns)"))
        elif gf and not cf:
            result.mismatches.append(TraceDiff(
                index=i, golden=gf,
                current=ProtocolFrame(i, 0),
                diff_type='missing',
                detail=f"Frame {i} present in golden but missing in current"))
        elif cf and not gf:
            result.mismatches.append(TraceDiff(
                index=i, golden=ProtocolFrame(i, 0),
                current=cf,
                diff_type='extra',
                detail=f"Frame {i} present in current but not in golden"))

    return result


def format_trace_report(result: TraceResult) -> str:
    """格式化 Golden Trace 报告"""
    RED = '\033[91m'; GREEN = '\033[92m'; YELLOW = '\033[93m'
    CYAN = '\033[96m'; BOLD = '\033[1m'; DIM = '\033[2m'; RESET = '\033[0m'

    lines = [
        f"{BOLD}{CYAN}EmbedAI Guard — Golden Trace Report{RESET}",
        f"  Golden:  {os.path.basename(result.golden_file)}",
        f"  Current: {os.path.basename(result.current_file)}",
        f"  Frames matched: {result.matched}",
        f"  Data errors:    {RED if result.data_errors else GREEN}"
        f"{result.data_errors}{RESET}",
        f"  Timing warnings:{YELLOW if result.timing_warnings else GREEN}"
        f" {result.timing_warnings}{RESET}",
        f"  Missing frames: {RED if result.missing_frames else GREEN}"
        f"{result.missing_frames}{RESET}",
        f"  Extra frames:   {YELLOW if result.extra_frames else GREEN}"
        f"{result.extra_frames}{RESET}",
        "",
    ]

    if result.passed:
        lines.append(f"  {GREEN}{BOLD}✓ Golden Trace PASSED — no differences{RESET}")
    else:
        lines.append(f"  {RED}{BOLD}✗ Differences found:{RESET}")
        for d in result.mismatches[:15]:
            icon = {'data': 'D', 'timing': 'T', 'missing': '-', 'extra': '+'}.get(d.diff_type, '?')
            color = RED if d.diff_type in ('data', 'missing') else YELLOW
            lines.append(f"    {color}[{icon}] Frame {d.index}: {d.detail}{RESET}")
        if len(result.mismatches) > 15:
            lines.append(f"    {DIM}... and {len(result.mismatches)-15} more{RESET}")

    return '\n'.join(lines)
