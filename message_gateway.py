#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Linux 微信消息网关抽象。

目的：
- 为未来的“非桌面自动化发送”预留统一中间件边界
- 将平台侧的发送请求与底层实现解耦
- 当前先提供协议与服务骨架，具体发送实现后续替换 backend

说明：
- 当前项目已经验证了数据库收消息链路
- 发送链路如果不走 UI 自动化，也不走 hook，就需要单独抽象成可替换 backend
- 本模块不假设已经掌握微信 Linux 客户端的发送协议，只定义统一接口
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import subprocess
import struct
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class GatewayMessage:
    """网关发送请求。"""

    target: str
    content: str
    msg_type: str = "text"
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class GatewayResult:
    """网关发送结果。"""

    success: bool
    message: str = ""
    message_id: Optional[str] = None
    provider: str = "null"
    raw_data: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "message": self.message,
            "message_id": self.message_id,
            "provider": self.provider,
            "raw_data": self.raw_data,
        }


@dataclass
class RecoveredMessageEntry:
    """当前静态逆向已确认的业务消息条目抽象。

    对应 `sub_909CD30` / `sub_909CA70` 中每 0x20 步长的一条输入消息记录。
    当前阶段已可初步理解为：
    - route_or_node: sid + 0xAAAA0000 + mode bits
    - begin_ptr/end_ptr: payload begin/end 抽象
    - cap_or_aux: payload cap / 附加字段
    """

    route_or_node: int = 0
    begin_ptr: int = 0
    end_ptr: int = 0
    cap_or_aux: int = 0
    payload_bytes: bytes = b""

    def to_bytes(self) -> bytes:
        """按当前已确认的 0x20 条目轮廓编码。"""
        return struct.pack(
            "<QQQQ",
            self.route_or_node & 0xFFFFFFFFFFFFFFFF,
            self.begin_ptr & 0xFFFFFFFFFFFFFFFF,
            self.end_ptr & 0xFFFFFFFFFFFFFFFF,
            self.cap_or_aux & 0xFFFFFFFFFFFFFFFF,
        )

    @property
    def payload_len(self) -> int:
        return max(0, self.end_ptr - self.begin_ptr)


@dataclass
class ChunkHeader12:
    """`sub_90A35D0 + sub_90A3610` 对应的 12 字节头。"""

    kind: int
    subkind: int
    main_id: int

    def to_bytes(self) -> bytes:
        header = bytearray(12)
        struct.pack_into(">H", header, 0x00, self.kind & 0xFFFF)
        struct.pack_into(">H", header, 0x02, self.subkind & 0xFFFF)
        struct.pack_into(">I", header, 0x04, self.main_id & 0xFFFFFFFF)
        return bytes(header)


@dataclass
class LinearBufferBuilder:
    """实验性 linear buffer builder。"""

    header: ChunkHeader12
    reserve_len: int = 0
    payload_objects: list[bytes] = field(default_factory=list)

    def append_payload(self, payload: bytes) -> None:
        self.payload_objects.append(bytes(payload))

    def build_chunk(self) -> bytes:
        data = bytearray(self.header.to_bytes())
        for payload in self.payload_objects:
            data.extend(payload)
        padding = (-len(data)) % 4
        if padding:
            data.extend(b"\x00" * padding)
        return bytes(data)


@dataclass
class RecoveredProtoItem56:
    """56 字节标准 item 的抽象骨架。"""

    item_type: int = 0
    payload_begin: int = 0
    payload_end: int = 0
    payload_cap: int = 0
    flag_word: int = 0
    flag_byte: int = 0
    extra_qword: int = 0
    valid: bool = False

    def to_bytes(self) -> bytes:
        """按当前已确认的 56 字节 item 轮廓编码。"""
        payload = struct.pack(
            "<I4xQQQHBB5xQ",
            self.item_type & 0xFFFFFFFF,
            self.payload_begin & 0xFFFFFFFFFFFFFFFF,
            self.payload_end & 0xFFFFFFFFFFFFFFFF,
            self.payload_cap & 0xFFFFFFFFFFFFFFFF,
            self.flag_word & 0xFFFF,
            self.flag_byte & 0xFF,
            1 if self.valid else 0,
            self.extra_qword & 0xFFFFFFFFFFFFFFFF,
        )
        return payload[:56].ljust(56, b"\x00")


@dataclass
class RecoveredBatchNode96:
    """96 字节批次节点的抽象骨架。"""

    node_type: int = 0
    qword0: int = 0
    qword1: int = 0
    qword2: int = 0
    qword3: int = 0
    payload_len: int = 0

    def to_bytes(self) -> bytes:
        """按当前已确认的 96 字节节点轮廓编码。"""
        data = bytearray(96)
        struct.pack_into("<I", data, 0x00, self.node_type & 0xFFFFFFFF)
        struct.pack_into("<Q", data, 0x08, self.qword0 & 0xFFFFFFFFFFFFFFFF)
        struct.pack_into("<Q", data, 0x10, self.qword1 & 0xFFFFFFFFFFFFFFFF)
        struct.pack_into("<Q", data, 0x18, self.qword2 & 0xFFFFFFFFFFFFFFFF)
        struct.pack_into("<Q", data, 0x20, self.qword3 & 0xFFFFFFFFFFFFFFFF)
        struct.pack_into("<Q", data, 0x48, self.payload_len & 0xFFFFFFFFFFFFFFFF)
        return bytes(data)


@dataclass
class RecoveredSlot88:
    """88 字节 source/slot 结构的实验性映射。"""

    id_or_type: int = 0
    context_qword_08: int = 0
    context_word_10: int = 0
    context_qword_18: int = 0
    context_qword_20: int = 0
    payload_begin: int = 0
    payload_end: int = 0
    payload_cap: int = 0
    payload_tail: int = 0
    flag_word: int = 0
    flag_byte: int = 0
    valid: bool = False

    def to_bytes(self) -> bytes:
        data = bytearray(88)
        struct.pack_into("<I", data, 0x00, self.id_or_type & 0xFFFFFFFF)
        struct.pack_into("<Q", data, 0x08, self.context_qword_08 & 0xFFFFFFFFFFFFFFFF)
        struct.pack_into("<H", data, 0x10, self.context_word_10 & 0xFFFF)
        struct.pack_into("<Q", data, 0x18, self.context_qword_18 & 0xFFFFFFFFFFFFFFFF)
        struct.pack_into("<Q", data, 0x20, self.context_qword_20 & 0xFFFFFFFFFFFFFFFF)
        struct.pack_into("<Q", data, 0x28, self.payload_begin & 0xFFFFFFFFFFFFFFFF)
        struct.pack_into("<Q", data, 0x30, self.payload_end & 0xFFFFFFFFFFFFFFFF)
        struct.pack_into("<Q", data, 0x38, self.payload_cap & 0xFFFFFFFFFFFFFFFF)
        struct.pack_into("<Q", data, 0x40, self.payload_tail & 0xFFFFFFFFFFFFFFFF)
        struct.pack_into("<H", data, 0x50, self.flag_word & 0xFFFF)
        struct.pack_into("<B", data, 0x52, self.flag_byte & 0xFF)
        struct.pack_into("<B", data, 0x50, 1 if self.valid else 0)
        return bytes(data)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id_or_type": self.id_or_type,
            "context_qword_08": self.context_qword_08,
            "context_word_10": self.context_word_10,
            "context_qword_18": self.context_qword_18,
            "context_qword_20": self.context_qword_20,
            "payload_begin": self.payload_begin,
            "payload_end": self.payload_end,
            "payload_cap": self.payload_cap,
            "payload_tail": self.payload_tail,
            "payload_len": max(0, self.payload_end - self.payload_begin),
            "flag_word": self.flag_word,
            "flag_byte": self.flag_byte,
            "valid": self.valid,
            "slot_88_hex": self.to_bytes().hex(),
        }


@dataclass
class RecoveredSendOptions:
    """当前静态逆向已确认的发送选项抽象。

    对应 `sub_90990B0` 中构造后传给 `sub_909CA70` 的 `char *a3` 结构。
    当前阶段仅保留已确认的轮廓。
    """

    unordered: bool = False
    enable_lifetime: bool = False
    lifetime_ms: int = 0
    enable_retransmit: bool = False
    retransmit_count: int = -1
    extra_context: int = 0
    option_type: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "unordered": self.unordered,
            "enable_lifetime": self.enable_lifetime,
            "lifetime_ms": self.lifetime_ms,
            "enable_retransmit": self.enable_retransmit,
            "retransmit_count": self.retransmit_count,
            "extra_context": self.extra_context,
            "option_type": self.option_type,
        }


@dataclass
class RecoveredActiveSessionState:
    """当前静态逆向已确认的活跃发送状态对象抽象。"""

    sender_ptr: int = 0
    sender_state: int = 0
    sender_flag: int = 0
    sender_config_ptr: int = 0
    sender_wrapper_ptr: int = 0
    queue_ptr: int = 0
    active_state_ptr: int = 0


@dataclass
class RecoveredTransportSession:
    """当前静态逆向已确认的 transport/session 子对象抽象。"""

    session_ptr: int = 0
    context_pool_ptr: int = 0
    send_state_flag: int = 0
    active_state_ptr: int = 0


@dataclass
class RecoveredTopLevelInterface:
    """当前静态逆向已确认的高层接口对象抽象。"""

    vtable_ptr: int = 0
    transport_ptr: int = 0
    send_data_fn: str = "sub_62BDD70"


@dataclass
class ExperimentalSendPlan:
    """实验性发送计划。

    当前阶段仅用于将已逆出的结构串起来，并输出可供继续比对的中间结果，
    不直接触发真实发送。
    """

    entry: RecoveredMessageEntry
    options: RecoveredSendOptions
    chunk_header_12: bytes
    item_header: bytes
    proto_item_56: bytes
    batch_node_96: bytes
    slot_88: bytes
    finalized_buffer: bytes

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entry": {
                "route_or_node": self.entry.route_or_node,
                "payload_hex": self.entry.payload_bytes.hex(),
                "begin_ptr": self.entry.begin_ptr,
                "end_ptr": self.entry.end_ptr,
                "cap_or_aux": self.entry.cap_or_aux,
                "payload_len": self.entry.payload_len,
                "entry_hex": self.entry.to_bytes().hex(),
            },
            "options": self.options.to_dict(),
            "chunk_header_12_hex": self.chunk_header_12.hex(),
            "item_header_hex": self.item_header.hex(),
            "proto_item_56_hex": self.proto_item_56.hex(),
            "batch_node_96_hex": self.batch_node_96.hex(),
            "slot_88_hex": self.slot_88.hex(),
            "finalized_buffer_hex": self.finalized_buffer.hex(),
            "finalized_buffer_len": len(self.finalized_buffer),
        }


def encode_item_header_12(
    word_a: int, word_b: int, dword_main: int, payload: bytes
) -> bytes:
    """复刻 `sub_90A3610` 的 12 字节头 + payload + 4 字节对齐。

    已确认行为：
    - 偏移 0: 16 位字段 A（大端）
    - 偏移 2: 16 位字段 B（大端）
    - 偏移 4: 32 位主字段（大端）
    - 偏移 8..11: 保留/补零
    - 之后写 payload，并做 4 字节对齐
    """
    header = bytearray(12)
    struct.pack_into(">H", header, 0x00, word_a & 0xFFFF)
    struct.pack_into(">H", header, 0x02, word_b & 0xFFFF)
    struct.pack_into(">I", header, 0x04, dword_main & 0xFFFFFFFF)
    body = bytes(header) + payload
    padding = (-len(body)) % 4
    return body + (b"\x00" * padding)


def align4(value: int) -> int:
    return (int(value) + 3) & ~3


def build_dynamic_validation_checklist() -> Dict[str, Any]:
    """输出当前最值得做的动态验证点。"""
    return {
        "watch_targets": [
            {
                "name": "queued_msg_96_context",
                "offset": "+0x38",
                "meaning": "queued_msg_96 上下文对象候选",
            },
            {
                "name": "slot_88_context_a",
                "offset": "+0x18",
                "meaning": "slot_88 前半区上下文 QWORD 候选 A",
            },
            {
                "name": "slot_88_context_b",
                "offset": "+0x20",
                "meaning": "slot_88 前半区上下文 QWORD 候选 B",
            },
            {
                "name": "slot_88_payload_begin_end",
                "offset": "+0x28/+0x30",
                "meaning": "slot_88 后半区 payload begin/end",
            },
            {
                "name": "sender_wrapper",
                "offset": "context+0x128",
                "meaning": "sub_90ADB20 的 sender wrapper 来源",
            },
        ],
        "breakpoints": [
            "sub_90BC570",
            "sub_90BAA30",
            "sub_90B7890",
            "sub_90ADB20",
        ],
        "expectations": {
            "text_payload": "begin/end 随消息正文长度变化",
            "context_object": "queued_msg_96 +0x38 与 slot_88 +0x18/+0x20 至少有一个对象链关联",
            "sender": "sub_90ADB20 的 a1 来自 context + 0x128",
        },
    }


def build_renderer_runtime_alignment() -> Dict[str, Any]:
    """描述当前静态主链与 renderer 真实发包链的关系。"""
    return {
        "status": "split_brain",
        "summary": [
            "静态主链已解释 message_entry_20 / payload_source / slot_88 / builder / CRC / sender wrapper 上层模型",
            "真实 renderer syscall(sendto,len=104) 已命中，但回溯偏移尚未直接映射到既有静态主链函数",
            "后续真实可发实现需同时对齐静态主链与 renderer 实际发包链",
            "sub_3463DC0 虽然静态上像统一提交桥，但当前未在真实发送现场稳定命中",
        ],
        "static_main_chain": [
            "sub_909CD30",
            "sub_90BB8E0",
            "sub_90B88F0",
            "sub_90BC570",
            "sub_90A39A0",
            "sub_90ADB20",
        ],
        "renderer_runtime_chain_pending": [
            "relative+0x5129",
            "relative+0x0e5023",
            "relative+0x0e782f",
            "relative+0x2f2a71",
            "relative+0x3d2d92",
            "relative+0x3d2e0d",
            "relative+0x3d8e03",
            "relative+0x4113dd",
            "relative+0x41198b",
            "relative+0x0adc7e5",
        ],
        "next_actions": [
            "在 IDA 中围绕 renderer 回溯偏移人工建函数并识别调用关系",
            "持续用 syscall/sendto 现场样本校准 104 字节控制帧偏移语义",
            "避免仅以静态主链或 sub_3463DC0 候选桥作为真实发包实现依据",
        ],
    }


def build_renderer_submit_hypothesis() -> Dict[str, Any]:
    """总结 renderer 提交层当前最可能可复用的对象模型。"""
    return {
        "submit_bridge": "sub_3463DC0",
        "submit_object": {
            "source_pattern": "ctx+8 -> a1",
            "shape": "sender/endpoint style object with vtable",
            "evidence": [
                "multiple small callers pass *(_QWORD *)(ctx+8) into sub_3463DC0",
                "sub_3463DC0 logs 'Send mojo message' and then calls a1->vfunc+24",
                "type-id registry layer (sub_346D6B0 / sub_346D740 / sub_346D7C0) appears distinct from submit object layer",
            ],
        },
        "candidate_object_mapping": {
            "a2": "target/session locator object",
            "a3": "main message content object",
            "a4": "optional extension metadata object",
            "a5": "scalar option / sequence / flags",
        },
        "static_chain_mapping_hint": {
            "a2": "target/session",
            "a3": "payload/source",
            "a4": "context/meta (context-heavy)",
        },
        "reuse_priority": [
            "confirm a1->vfunc+24 implementation target",
            "confirm a1 concrete object family / vtable owner",
            "map static-chain target/content/meta objects onto renderer submit objects",
        ],
        "runtime_status": "not_yet_hit_on_real_send_path",
    }


def build_renderer_downstream_focus() -> Dict[str, Any]:
    """总结 renderer 真实发包链当前最值得继续追的下游层。"""
    return {
        "primary_target": "sub_46D64E0",
        "reason": "deeper downstream analysis shows sub_46D64E0 is the strongest execution bridge immediately below the dispatch/write entry, with full fast/slow/fallback paths identified",
        "candidate_chain": [
            "sub_37842E0",
            "sub_46D64E0",
            "sub_5FC7840",
            "sub_5FCFC30",
            "sub_3B6CEA0",
            "sub_5FD20D0",
            "sub_5FD2300",
            "sub_5FD6DC0",
            "sub_5FD89C0",
            "sub_46D7610",
            "sub_46D83A0",
            "sub_46D70D0",
            "sub_46D7260",
            "sub_46D7570",
            "sub_3B6D740",
            "sub_3B6C390",
            "sub_5FBF530",
            "sub_5FBF760",
            "sub_5FD7C00",
            "sub_5FC7E60",
            "sub_3B6E350",
            "sub_3B6E7E0",
            "sub_5FBD360",
            "sub_5FBDE30",
            "syscall sendto(len=104)",
        ],
        "supporting_functions": [
            "sub_5FCDCB0",
            "sub_46D8A10",
            "sub_46DC444",
            "sub_5FD1C70",
            "sub_46D2E00",
            "sub_46D2EF0",
            "sub_46D2F90",
            "sub_46D3030",
        ],
        "current_judgement": {
            "sub_37842E0": "recursive dispatcher / child expander (mode=1 root, mode=0 children)",
            "sub_46D64E0": "execution bridge: materialize transition from handle, with fast/slow/fallback paths",
            "sub_5FC7840": "lookup_or_build_and_cache_child wrapper around sub_5FCFC30",
            "sub_5FCFC30": "root entry / 0x70 outbound node builder",
            "sub_3B6CEA0": "encode_or_normalize_transition_payload (shared by sub_46D64E0 and sub_5FD6DC0)",
            "sub_5FD20D0": "attach_result_and_continue_if_needed (may re-enter sub_37842E0)",
            "sub_5FD2300": "fallback_transition_orchestration",
            "sub_5FD6DC0": "heavy sub-path of sub_37842E0: complex object graph sync, calls sub_5FC7840/sub_3B6CEA0/sub_5FD20D0/sub_37842E0",
            "sub_5FD89C0": "tiny wrapper around sub_5FD6DC0 + cleanup",
            "sub_46D7610": "release_and_unlink_small_handle (unified cleanup)",
            "sub_46D83A0": "upsert_record_by_key_and_notify_if_new (hash container insertion)",
            "sub_46D70D0": "submit_or_invoke_wrapper_event (fast-path submitter)",
            "sub_46D7260": "acquire_descriptor_for_key_64 (descriptor extractor)",
            "sub_46D7570": "deferred_reenter_sub_46D64E0_callback",
            "sub_3B6D740": "shared dispatch/dispatch engine (observer pattern, batch dispatch via sub_3B6E350)",
            "sub_3B6C390": "notify_new_key_via_cached_observer_or_lazy_sink",
            "sub_5FBF530": "new-key-first-insertion direct notifier",
            "sub_5FBF760": "route_or_queue_pair_key_notification (pair-key router/queuer)",
            "sub_5FD7C00": "sorting/span reordering helper (introsort family, not final write bridge)",
            "sub_5FC7E60": "construct_and_dispatch: builds payload from context, calls sub_3B6D740",
            "sub_3B6E350": "batch item processor: iterates array, calls virtual functions on each item",
            "sub_3B6E7E0": "internal storage allocator/expander, used by observer and batch dispatch",
            "sub_5FBD360": "internal storage expander (vector-like append with resize)",
            "sub_5FBDE30": "array element inserter (allocates new backing, moves old, inserts new)",
        },
        "next_actions": [
            "probe sub_46D64E0 directly on the active renderer pid",
            "trace sub_3B6D740 as the shared delivery/dispatch base (observer pattern, not direct IPC send)",
            "map any stable object fields in the execution bridge path back to static-chain payload/context models",
        ],
        "complete_chain_summary": {
            "fast_path": "sub_46D64E0 -> sub_46D70D0 -> sub_46D7260(key=64 descriptor) -> sub_46D83A0(upsert) -> sub_5FBF530(notify)",
            "slow_path": "sub_46D64E0 -> sub_5FD7C00(lock ordering) -> sub_5FC7840 -> sub_5FCFC30(build child) -> sub_3B6CEA0(encode transition) -> sub_5FD20D0(attach and continue) -> sub_37842E0(maybe recurse)",
            "fallback_path": "sub_46D64E0 -> sub_5FD2300(fallback orchestration) -> sub_37842E0(recurse)",
            "heavy_sub_path": "sub_37842E0 -> sub_5FD6DC0 -> sub_5FC7840/sub_3B6CEA0/sub_5FD20D0/sub_37842E0 (complex object graph sync)",
            "cleanup": "sub_46D7610 called throughout to release/unlink temporary handles",
            "deferred_callback": "sub_46D7570 registered in fast path payload, re-enters sub_46D64E0",
            "broader_notification": "sub_5FBF760 routes/queues pair-key notifications, shares sub_3B6D740 with sub_3B6CEA0",
            "delivery_core": "sub_3B6D740 is the shared dispatch/dispatch engine (observer pattern), called by both sub_3B6CEA0 and sub_5FBF760",
            "observer_registration": "sub_3B6C390 registers new observers by key, builds 0x30 node, links into doubly-linked list, then calls sub_3B6D740",
            "batch_dispatch": "sub_3B6E350 processes array of items, calls virtual functions on each, used by sub_3B6D740 slow path",
            "buffer_management": "sub_3B6E7E0 allocates/expands internal storage, used by sub_3B6C390 and sub_3B6E350",
            "construct_and_dispatch": "sub_5FC7E60 builds a payload message from a3 context, appends to internal vector, then calls sub_3B6D740",
            "vector_operations": "sub_5FBD360/sub_5FBDE30 are dynamic array management (expand/insert), not business logic",
        },
    }


def build_reverse_status_report() -> Dict[str, Any]:
    """输出当前阶段可交付的逆向状态报告。"""
    return {
        "phase": "protocol_model_ready_but_submit_path_unresolved",
        "confirmed": {
            "message_entry_20": {
                "payload_begin_offset": "+0x08",
                "payload_end_offset": "+0x10",
                "summary": "正文 payload 在上游业务条目中以 begin/end 三元组形式存在",
            },
            "context_split": {
                "summary": "联系人/会话上下文与正文 payload 分离传递",
                "slot_88_front": ["+0x08", "+0x10", "+0x18", "+0x20"],
                "slot_88_back": ["+0x28", "+0x30", "+0x38", "+0x40"],
            },
            "builder": {
                "chunk_header_len": 12,
                "crc_offset": "+0x08",
                "alignment": 4,
            },
            "sender_wrapper": {
                "static_source": "context+0x128",
                "summary": "静态主链中的 sender wrapper 来源已高可信锁定",
            },
            "renderer_sendto_runtime": {
                "confirmed": True,
                "primary_pid_hint": 9498,
                "message_type_hint": "0x10",
                "stage_hint": "0x0002",
            },
        },
        "not_reconfirmed_anymore": [
            "sub_909CBD0 is validator, not payload/context constructor",
            "sub_90B65C0 and sub_90B7130 are scheduler/state helpers",
            "slot_88 front/back split has enough evidence",
        ],
        "remaining_blockers": [
            "real source/type of sub_3463DC0 a1 submit object",
            "real implementation/module behind a1->vfunc+24",
            "stable mapping from sub_346D420 a2/a3/a4 to static-chain target/content/meta objects",
        ],
        "delivery_recommendation": {
            "do_now": "preserve current backend as structural model and debugging surface",
            "do_next": "continue renderer runtime chain identification from stable syscall samples rather than expanding upper static chain",
            "avoid": "do not claim real send capability before submit object and vfunc+24 are nailed down",
        },
    }


def crc32c_python(data: bytes, seed: int = 0) -> int:
    """纯 Python CRC32C 实现。

    目的：为后续验证 `sub_90A39A0 -> sub_90AC5A0 -> sub_4CC6FA0`
    链路提供一个可直接使用的实验性校验器。
    """
    poly = 0x82F63B78
    crc = (~seed) & 0xFFFFFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            mask = -(crc & 1) & 0xFFFFFFFF
            crc = ((crc >> 1) ^ (poly & mask)) & 0xFFFFFFFF
    return (~crc) & 0xFFFFFFFF


def finalize_buffer_with_crc32c(buf: bytes, apply_crc: bool = True) -> bytes:
    """复刻 `sub_90A39A0` 的最终 buffer 收尾逻辑。

    当前阶段：
    - 仅在 `apply_crc=True` 且长度足够时回填 `buf+8`
    - CRC 使用当前已高度怀疑的 CRC32C
    """
    if not apply_crc or len(buf) <= 11:
        return buf

    data = bytearray(buf)
    struct.pack_into(">I", data, 0x08, 0)
    crc = crc32c_python(bytes(data))
    struct.pack_into(">I", data, 0x08, crc)
    return bytes(data)


def derive_mode_qword(option_type: int, payload_len: int) -> int:
    """根据 `sub_90990B0` 当前已确认逻辑推导 mode bits。

    已确认逻辑：
    - if (*(_DWORD *)v52 == 2)      v35 = 0x3200000000
    - elif (*(_DWORD *)v52 == 1)    v35 = ((n == 0) << 34) + 0x3500000000
    - else                          v35 = 0x3300000000; if (!n) v35 = 0x3800000000

    这里返回的是直接并入 entry.route_or_node 的 64 位高位部分。
    """
    is_empty = payload_len == 0
    if option_type == 2:
        return 0x3200000000
    if option_type == 1:
        return 0x3500000000 + ((1 << 34) if is_empty else 0)
    return 0x3800000000 if is_empty else 0x3300000000


class BaseGatewayBackend(ABC):
    """消息网关后端抽象。"""

    name = "base"

    @abstractmethod
    def send(self, message: GatewayMessage) -> GatewayResult:
        raise NotImplementedError

    def capabilities(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "supports_text": True,
            "supports_image": False,
            "supports_file": False,
            "supports_group": False,
            "status": "abstract",
        }


def normalize_gateway_result(
    result: GatewayResult, fallback_provider: str = "unknown"
) -> GatewayResult:
    """规范化发送结果，避免上层处理大量空值判断。"""
    provider = result.provider or fallback_provider
    raw_data = result.raw_data if isinstance(result.raw_data, dict) else {}
    return GatewayResult(
        success=bool(result.success),
        message=str(result.message or ""),
        message_id=(str(result.message_id) if result.message_id else None),
        provider=provider,
        raw_data=raw_data,
    )


class NullGatewayBackend(BaseGatewayBackend):
    """占位后端。

    用于当前未实现真正发送协议时，返回明确错误，而不是让调用链崩溃。
    """

    name = "null"

    def send(self, message: GatewayMessage) -> GatewayResult:
        logger.warning(
            "Linux 微信消息网关收到发送请求，但当前未配置可用 backend: target=%s type=%s",
            message.target,
            message.msg_type,
        )
        return GatewayResult(
            success=False,
            message=(
                "Linux 微信发送网关当前未配置可用 backend。"
                "如果不走桌面自动化/Hook，需要继续实现协议级发送后端。"
            ),
            provider=self.name,
            raw_data={"target": message.target, "msg_type": message.msg_type},
        )

    def capabilities(self) -> Dict[str, Any]:
        caps = super().capabilities()
        caps.update({"status": "placeholder"})
        return caps


class ReverseEngineeredGatewayBackend(BaseGatewayBackend):
    """协议级发送后端骨架。

    这是未来真正实现“非 UI 自动化发送”的承载点。
    当前仅作为结构占位，不做虚假实现。
    """

    name = "reverse_engineered"

    def __init__(self, runtime_config: Optional[Dict[str, Any]] = None):
        self.runtime_config = runtime_config or {}

        # 当前静态逆向已经确认的主线：
        # 1. sub_62BDD70   -> DataChannel/上层 SendData(...) 包装器
        # 2. sub_62D08E0   -> sid 相关控制包装器（经 +64 上下文）
        # 3. sub_62D0900   -> sid 相关控制包装器（经 +56 上下文）
        # 4. sub_62BDE10   -> sid 控制方法 1（经 a1+16->+56）
        # 5. sub_62BDE30   -> sid 控制方法 2（经 a1+16->+56）
        # 6. sub_62BDE50   -> OnChannelStateChanged 回调方法
        # 7. sub_62BE060   -> 通过 +1E0 上下文池查询（sub_90BBE50）
        # 8. sub_62BE090   -> 通过 +1E0 上下文池更新（sub_90BBEA0）
        # 9. sub_62BE0C0   -> 通过 +1E0 上下文池推进（sub_90BBEF0）
        # 7. sub_62BDC80   -> 该高层接口对象的析构/重置函数（非构造器）
        # 8. sub_62BDC50   -> 相关类对象（off_C37F910）的析构/销毁函数
        # 10. sub_909CBD0  -> 单条消息发送前校验
        # 11. sub_90990B0  -> 上层单条聊天消息发送 API 风格入口
        # 12. sub_909CA70  -> 单条聊天消息发送入口
        # 13. sub_909CD30  -> 聊天发送主调度器（多条批处理）
        # 8. sub_90BB8E0   -> 单条业务消息 -> 中间发送单元
        # 9. sub_90BA860   -> 单元入批次
        # 10. sub_90BA980  -> 96字节批次节点写入入口
        # 11. sub_90BC570  -> 96字节批次节点填充
        # 12. sub_90B88F0  -> 批量 item 聚合为聊天发送请求
        # 13. sub_90A3610  -> 12字节头 + payload 编码
        # 14. sub_90A39A0  -> 最终 buffer 收尾 + CRC32C 回填
        # 15. sub_90ADB20  -> 统一最终发送提交口
        # 16. sub_90B8F70  -> 活跃发送状态对象输入绑定桥
        # 17. sub_90B9380  -> sender 包装器（a1+296, a1+211）
        # 18. sub_346B750  -> Mojo 通用桥
        # 19. libmmmojo.so -> 通道层
        #
        # 当前最接近可复刻的中间结构：
        # - 56 字节标准 item
        # - 88 字节槽位结构
        # - 96 字节批次节点结构
        # - transport/session 子对象
        # - 顶层 SendData 接口对象
        #
        # 当前 sender 相关关键偏移（阶段性结论）：
        # - +128 : sender / 发送对象引用
        # - +130 : sender 相关状态值
        # - +134 : sender 标志位
        # - +138 : 发送窗口/超时配置结构
        # - +178 : 发送上下文入口字段
        # - +1A0 : 回调/方法指针
        # - +1B8 : sender wrapper / 最终提交上下文
        # - +211 : mode flag，调用前通常会 xor 1
        # - +2A0 : 批次/队列管理结构
        # - +296 : sender 包装对象（sub_90B9380 / sub_90ADB20 入口）
        #
        # 当前 sender 状态对象主线（阶段性结论）：
        # - 上层业务对象 rbx
        # - sub_909C020      -> 检查并准备会话状态
        # - sub_90BC0A0      -> 初始化 +0x1E0 区域内的类型上下文池
        # - sub_909BEB0      -> 构造新的聊天发送核心状态对象
        # - sub_90B7B20      -> 初始化 sender / wrapper / queue / timer 结构
        # - rbx + 0x2A8      -> 当前活跃发送状态对象指针
        # - sub_90B8F70      -> 将上游输入绑定到当前发送状态对象
        # - sub_62BDD70      -> 通过 transport->+56 取上下文后调用 sub_90990B0
        # - sub_9098990      -> 更高层容器对象初始化器
        # - sub_909AFE0      -> transport/session 子对象初始化器
        # - 0xC37F910 / 0xC37F940 可能对应一组相关类（基类/派生类或并列接口对象）
        # - sub_90A14D0      -> 更高层容器对象的析构/清理路径，说明这些接口对象挂在更大的容器对象里
        #
        # 未来 backend 需要解决的问题：
        # - 如何获取/构造高层接口对象实例
        # - 如何获取/构造 transport/session 子对象
        # - 如何构造消息条目数组
        # - 如何构造/获取 sender 对象
        # - 如何将业务内容转换成 56 字节 item
        # - 如何将单元压入 96 字节批次节点
        # - 如何生成最终线性 buffer 并回填 CRC32C
        # - 最终如何提交到 sender 虚表接口

    def build_entry(self, message: GatewayMessage) -> RecoveredMessageEntry:
        """构建实验性的 0x20 消息条目。"""
        sid = int(self.runtime_config.get("sid", 1)) & 0xFFFF
        payload = message.content.encode("utf-8") if message.msg_type == "text" else b""
        options = self.build_send_options(message)
        mode_qword = derive_mode_qword(options.option_type, len(payload))
        route_or_node = sid | 0xAAAA0000 | mode_qword
        begin_ptr = 0
        end_ptr = len(payload)
        cap_or_aux = align4(len(payload))
        return RecoveredMessageEntry(
            route_or_node=route_or_node,
            begin_ptr=begin_ptr,
            end_ptr=end_ptr,
            cap_or_aux=cap_or_aux,
            payload_bytes=payload,
        )

    def build_send_options(self, message: GatewayMessage) -> RecoveredSendOptions:
        """构建实验性的发送选项结构。"""
        metadata = message.metadata or {}
        return RecoveredSendOptions(
            unordered=bool(metadata.get("unordered", False)),
            enable_lifetime=bool(metadata.get("enable_lifetime", False)),
            lifetime_ms=int(metadata.get("lifetime_ms", 0) or 0),
            enable_retransmit=bool(metadata.get("enable_retransmit", False)),
            retransmit_count=int(metadata.get("retransmit_count", -1) or -1),
            extra_context=int(metadata.get("extra_context", 0) or 0),
            option_type=int(metadata.get("option_type", 0) or 0),
        )

    def build_item_header(self, entry: RecoveredMessageEntry) -> bytes:
        """基于当前推断构建 item 头 + payload。"""
        word_a = (entry.route_or_node >> 16) & 0xFFFF
        word_b = entry.route_or_node & 0xFFFF
        dword_main = (entry.route_or_node >> 32) & 0xFFFFFFFF
        payload = entry.payload_bytes
        return encode_item_header_12(word_a, word_b, dword_main, payload)

    def build_chunk_header(self, entry: RecoveredMessageEntry) -> ChunkHeader12:
        return ChunkHeader12(
            kind=(entry.route_or_node >> 16) & 0xFFFF,
            subkind=entry.route_or_node & 0xFFFF,
            main_id=(entry.route_or_node >> 32) & 0xFFFFFFFF,
        )

    def build_proto_item_56(
        self, entry: RecoveredMessageEntry, payload: bytes
    ) -> RecoveredProtoItem56:
        """构建实验性的 56 字节标准 item。"""
        payload_len = len(payload)
        flag_word = 0x0100 if not payload else 0x0001
        flag_byte = 1 if payload else 0
        return RecoveredProtoItem56(
            item_type=(entry.route_or_node >> 32) & 0xFFFFFFFF,
            payload_begin=entry.begin_ptr,
            payload_end=entry.end_ptr,
            payload_cap=entry.cap_or_aux,
            flag_word=flag_word,
            flag_byte=flag_byte,
            extra_qword=entry.route_or_node,
            valid=True,
        )

    def build_batch_node_96(
        self, entry: RecoveredMessageEntry, item_56: RecoveredProtoItem56
    ) -> RecoveredBatchNode96:
        """构建实验性的 96 字节批次节点。"""
        payload_len = max(0, item_56.payload_end - item_56.payload_begin)
        return RecoveredBatchNode96(
            node_type=item_56.item_type,
            qword0=entry.route_or_node,
            qword1=item_56.payload_begin,
            qword2=item_56.payload_end,
            qword3=item_56.payload_cap,
            payload_len=payload_len,
        )

    def build_slot_88(
        self,
        entry: RecoveredMessageEntry,
        item_56: RecoveredProtoItem56,
        batch_node: RecoveredBatchNode96,
    ) -> RecoveredSlot88:
        """基于当前已确认偏移构建实验性 88 字节槽位。"""
        metadata = entry.route_or_node & 0xFFFFFFFF
        return RecoveredSlot88(
            id_or_type=item_56.item_type,
            context_qword_08=0,
            context_word_10=0,
            context_qword_18=batch_node.qword0,
            context_qword_20=batch_node.qword3,
            payload_begin=entry.begin_ptr,
            payload_end=entry.end_ptr,
            payload_cap=entry.cap_or_aux,
            payload_tail=batch_node.payload_len,
            flag_word=item_56.flag_word,
            flag_byte=item_56.flag_byte,
            valid=bool(metadata or entry.payload_len),
        )

    def build_linear_buffer(self, entry: RecoveredMessageEntry) -> tuple[bytes, bytes]:
        """基于当前已知 builder 模型构建实验性最终 buffer。"""
        header = self.build_chunk_header(entry)
        builder = LinearBufferBuilder(
            header=header,
            reserve_len=align4(12 + len(entry.payload_bytes)),
        )
        builder.append_payload(entry.payload_bytes)
        chunk = builder.build_chunk()
        finalized = finalize_buffer_with_crc32c(
            chunk,
            apply_crc=bool(self.runtime_config.get("apply_crc", True)),
        )
        return header.to_bytes(), finalized

    def build_plan(self, message: GatewayMessage) -> ExperimentalSendPlan:
        """构建实验性发送计划并输出中间结构。"""
        entry = self.build_entry(message)
        options = self.build_send_options(message)
        chunk_header_12, finalized_buffer = self.build_linear_buffer(entry)
        item_header = self.build_item_header(entry)
        payload = entry.payload_bytes
        proto_item = self.build_proto_item_56(entry, payload)
        proto_item_56 = proto_item.to_bytes()
        batch_node = self.build_batch_node_96(entry, proto_item)
        batch_node_96 = batch_node.to_bytes()
        slot_88 = self.build_slot_88(entry, proto_item, batch_node).to_bytes()
        return ExperimentalSendPlan(
            entry=entry,
            options=options,
            chunk_header_12=chunk_header_12,
            item_header=item_header,
            proto_item_56=proto_item_56,
            batch_node_96=batch_node_96,
            slot_88=slot_88,
            finalized_buffer=finalized_buffer,
        )

    def send(self, message: GatewayMessage) -> GatewayResult:
        plan = self.build_plan(message)
        logger.info(
            "[ExperimentalGatewayBackend] built plan for target=%s type=%s len=%s",
            message.target,
            message.msg_type,
            len(plan.finalized_buffer),
        )
        return GatewayResult(
            success=False,
            message=(
                "ReverseEngineeredGatewayBackend 当前仅完成实验性结构构建，"
                "已生成 entry / item header / finalized buffer，"
                "但尚未获得可安全调用的 sender 实例，因此不执行真实发送。"
            ),
            provider=self.name,
            raw_data={
                "target": message.target,
                "msg_type": message.msg_type,
                "metadata": message.metadata,
                "experimental_plan": plan.to_dict(),
                "dynamic_validation": build_dynamic_validation_checklist(),
                "renderer_runtime_alignment": build_renderer_runtime_alignment(),
                "renderer_submit_hypothesis": build_renderer_submit_hypothesis(),
                "renderer_downstream_focus": build_renderer_downstream_focus(),
                "reverse_status_report": build_reverse_status_report(),
                "reverse_progress": {
                    "validated_chain": [
                        "sub_62BDD70",
                        "sub_62D08E0",
                        "sub_62D0900",
                        "sub_62BDE10",
                        "sub_62BDE30",
                        "sub_62BDE50",
                        "sub_909CBD0",
                        "sub_90990B0",
                        "sub_909CA70",
                        "sub_909CD30",
                        "sub_90BB8E0",
                        "sub_90BA860",
                        "sub_90BA980",
                        "sub_90BC570",
                        "sub_90B88F0",
                        "sub_90B8F70",
                        "sub_90A3610",
                        "sub_90A39A0",
                        "sub_90ADB20",
                        "sub_90B9380",
                        "sub_90B5710",
                        "sub_90B5830",
                        "sub_346B750",
                    ],
                    "known_structures": {
                        "message_entry_20_begin_end": True,
                        "slot_88_front_vs_back_split": True,
                        "chunk_header_12": True,
                        "item_56": True,
                        "slot_88": True,
                        "batch_node_96": True,
                        "transport_session_object": True,
                        "top_level_interface_object": True,
                        "crc32c_finalizer": True,
                    },
                    "next_focus": [
                        "source object producer for context+0x198",
                        "56-byte node producer: sub_90B6EF0 / sub_90B6D20 / sub_90BCD90",
                        "text payload serializer vfunc used by sub_90A3610",
                        "sender wrapper object source (+296 / +128)",
                        "real IPC submission path behind sender vfunc+24",
                    ],
                },
            },
        )

    def debug_plan(self, message: GatewayMessage) -> str:
        """生成适合日志/文件保存的调试文本。"""
        plan = self.build_plan(message)
        data = {
            "target": message.target,
            "msg_type": message.msg_type,
            "content": message.content,
            "metadata": message.metadata,
            "plan": plan.to_dict(),
        }
        return json.dumps(data, ensure_ascii=False, indent=2)

    def capabilities(self) -> Dict[str, Any]:
        caps = super().capabilities()
        caps.update(
            {
                "supports_group": True,
                "supports_image": True,
                "status": "planned",
            }
        )
        return caps


class GuiRemoteGatewayBackend(BaseGatewayBackend):
    """基于远端 X11 工具的临时可用发送后端。"""

    name = "gui_remote"

    def __init__(self, runtime_config: Optional[Dict[str, Any]] = None):
        self.runtime_config = runtime_config or {}

    def _build_remote_script(self, message: GatewayMessage) -> str:
        display = str(self.runtime_config.get("display") or ":1")
        xauthority = str(
            self.runtime_config.get("xauthority") or "/home/sky/.Xauthority"
        )
        window_class = str(self.runtime_config.get("window_class") or "wechat.wechat")
        target = str(message.target or "")
        content = str(message.content or "")
        if not content:
            raise ValueError("发送内容不能为空")

        escaped_target = json.dumps(target, ensure_ascii=False)
        escaped_content = json.dumps(content, ensure_ascii=False)
        escaped_display = json.dumps(display)
        escaped_xauthority = json.dumps(xauthority)
        escaped_window_class = json.dumps(window_class)

        return f"""
import os
import subprocess
import time

env = os.environ.copy()
env['DISPLAY'] = {escaped_display}
env['XAUTHORITY'] = {escaped_xauthority}

window_class = {escaped_window_class}
target = {escaped_target}
content = {escaped_content}

WIN_ID = '0x00800013'
WIN_X = 117
WIN_Y = 0
WIN_W = 980
WIN_H = 694

def key(*args):
    subprocess.run(['xdotool', *args], env=env, check=False)

def clip(text):
    p = subprocess.Popen(['xclip', '-selection', 'clipboard'], env=env, stdin=subprocess.PIPE)
    p.communicate(text.encode('utf-8'))

def click(x, y):
    subprocess.run(['xdotool', 'mousemove', '--sync', str(x), str(y)], env=env, check=False)
    time.sleep(0.1)
    subprocess.run(['xdotool', 'click', '1'], env=env, check=False)

subprocess.run(['wmctrl', '-xa', window_class], env=env, check=False)
subprocess.run(['wmctrl', '-ia', WIN_ID], env=env, check=False)
time.sleep(1.2)

target_aliases = [target]
if target.lower() == 'filehelper':
    target_aliases.extend(['文件传输助手', 'File Transfer Assistant'])

selected = False
for alias in target_aliases:
    if not alias:
        continue
    # 左侧搜索框
    click(WIN_X + 150, WIN_Y + 40)
    time.sleep(0.3)
    key('key', '--clearmodifiers', 'ctrl+a')
    time.sleep(0.1)
    key('key', '--clearmodifiers', 'BackSpace')
    time.sleep(0.1)
    clip(alias)
    key('key', '--clearmodifiers', 'ctrl+v')
    time.sleep(1.0)
    # 搜索结果首项
    click(WIN_X + 180, WIN_Y + 120)
    time.sleep(0.2)
    key('key', '--clearmodifiers', 'Return')
    time.sleep(1.2)
    selected = True
    break

if not selected:
    raise SystemExit('failed to select target chat')

# 底部输入框
click(WIN_X + 620, WIN_Y + 650)
time.sleep(0.2)
clip(content)
key('key', '--clearmodifiers', 'ctrl+v')
time.sleep(0.25)
key('key', '--clearmodifiers', 'Return')
print('sent')
"""

    def _run_remote(self, script: str) -> str:
        host = str(self.runtime_config.get("host") or "").strip()
        port = int(self.runtime_config.get("port") or 22)
        username = str(self.runtime_config.get("username") or "").strip()
        password = str(self.runtime_config.get("password") or "").strip()
        helper = self.runtime_config.get("remote_exec_helper") or os.path.join(
            os.getcwd(), "tools", "remote_exec.py"
        )

        if not all([host, username, password]):
            raise RuntimeError("gui_remote backend 缺少 host/username/password 配置")
        if not os.path.exists(helper):
            raise RuntimeError(f"未找到远端执行辅助脚本: {helper}")

        command = [
            "python",
            helper,
            "--host",
            host,
            "--port",
            str(port),
            "--username",
            username,
            "--password",
            password,
            "--timeout",
            str(int(self.runtime_config.get("remote_timeout") or 30)),
            "--script",
            script,
        ]
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
        output = (completed.stdout or "") + (completed.stderr or "")
        if completed.returncode != 0:
            raise RuntimeError(output.strip() or "远端 GUI 发送执行失败")
        return output.strip()

    def send(self, message: GatewayMessage) -> GatewayResult:
        script = self._build_remote_script(message)
        output = self._run_remote(script)
        return GatewayResult(
            success=True,
            message="已通过远端 GUI 自动化触发发送",
            message_id=build_request_id(message.target, message.content),
            provider=self.name,
            raw_data={
                "target": message.target,
                "msg_type": message.msg_type,
                "remote_output": output,
            },
        )

    def capabilities(self) -> Dict[str, Any]:
        caps = super().capabilities()
        caps.update({"supports_group": True, "status": "temporary_working"})
        return caps


def create_gateway_backend(
    runtime_config: Optional[Dict[str, Any]] = None,
) -> BaseGatewayBackend:
    """按配置创建 Linux 微信消息网关 backend。"""
    config = runtime_config or {}
    backend_name = str(config.get("backend") or "reverse_engineered").strip().lower()

    if backend_name in {"reverse_engineered", "reverse", "re"}:
        return ReverseEngineeredGatewayBackend(runtime_config=config)
    if backend_name in {"gui_remote", "remote_gui", "x11_gui"}:
        return GuiRemoteGatewayBackend(runtime_config=config)
    if backend_name in {"null", "disabled", "placeholder"}:
        return NullGatewayBackend()

    logger.warning("未知 Linux 微信消息网关 backend=%s，回退到 null", backend_name)
    return NullGatewayBackend()


def send_gateway_message(
    target: str,
    content: str,
    msg_type: str = "text",
    metadata: Optional[Dict[str, Any]] = None,
    runtime_config: Optional[Dict[str, Any]] = None,
) -> GatewayResult:
    """统一封装 Linux 微信网关发送调用。"""
    backend = create_gateway_backend(runtime_config=runtime_config)
    message = GatewayMessage(
        target=str(target),
        content=str(content),
        msg_type=str(msg_type or "text"),
        metadata=metadata or {},
    )
    result = backend.send(message)
    return normalize_gateway_result(result, fallback_provider=backend.name)


def build_signature(payload: Dict[str, Any], secret_key: str) -> str:
    body = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hmac.new(
        secret_key.encode("utf-8"), body.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def verify_signature(payload: Dict[str, Any], secret_key: Optional[str]) -> bool:
    if not secret_key:
        return True
    provided = str(payload.get("signature") or "")
    unsigned_payload = {k: v for k, v in payload.items() if k != "signature"}
    expected = build_signature(unsigned_payload, secret_key)
    return hmac.compare_digest(provided, expected)


def build_request_id(target: str, content: str) -> str:
    raw = f"{target}:{content}:{time.time_ns()}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
