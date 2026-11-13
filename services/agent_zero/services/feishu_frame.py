"""飞书长连接帧（pbbp2.proto）的最小 protobuf 编解码。

只实现 `Header` / `Frame` 两个消息，避免把整个 95MB 的 lark-oapi SDK
（其中 92MB 是生成的 OpenAPI 客户端）打进桌面端。

protobuf 线格式：
    Frame {
      uint64 SeqID = 1; uint64 LogID = 2; int32 service = 3; int32 method = 4;
      repeated Header headers = 5; string payload_encoding = 6;
      string payload_type = 7; bytes payload = 8; string LogIDNew = 9;
    }
    Header { string key = 1; string value = 2; }
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Header:
    key: str = ""
    value: str = ""


@dataclass
class Frame:
    SeqID: int = 0
    LogID: int = 0
    service: int = 0
    method: int = 0
    headers: list[Header] = field(default_factory=list)
    payload_encoding: str = ""
    payload_type: str = ""
    payload: bytes = b""
    LogIDNew: str = ""

    def add_header(self, key: str, value: str) -> None:
        self.headers.append(Header(key=key, value=value))

    @classmethod
    def FromString(cls, data: bytes) -> "Frame":
        frame = cls()
        index = 0
        size = len(data)
        while index < size:
            tag, index = _read_varint(data, index)
            number, wire = tag >> 3, tag & 7
            if wire == 0:
                value, index = _read_varint(data, index)
            elif wire == 2:
                length, index = _read_varint(data, index)
                end = index + length
                if end > size:
                    raise ValueError("飞书帧字段长度越界")
                value = data[index:end]
                index = end
            elif wire == 1:
                index += 8
                continue
            elif wire == 5:
                index += 4
                continue
            else:
                raise ValueError(f"不支持的 protobuf wire type: {wire}")

            if number == 1:
                frame.SeqID = int(value)
            elif number == 2:
                frame.LogID = int(value)
            elif number == 3:
                frame.service = int(value)
            elif number == 4:
                frame.method = int(value)
            elif number == 5:
                frame.headers.append(_parse_header(value))
            elif number == 6:
                frame.payload_encoding = value.decode("utf-8", errors="replace")
            elif number == 7:
                frame.payload_type = value.decode("utf-8", errors="replace")
            elif number == 8:
                frame.payload = bytes(value)
            elif number == 9:
                frame.LogIDNew = value.decode("utf-8", errors="replace")
        return frame

    def SerializeToString(self) -> bytes:
        parts = [
            _field_varint(1, self.SeqID),
            _field_varint(2, self.LogID),
            _field_varint(3, self.service),
            _field_varint(4, self.method),
        ]
        for header in self.headers:
            parts.append(_field_bytes(5, _serialize_header(header)))
        if self.payload_encoding:
            parts.append(_field_bytes(6, self.payload_encoding.encode()))
        if self.payload_type:
            parts.append(_field_bytes(7, self.payload_type.encode()))
        if self.payload:
            parts.append(_field_bytes(8, self.payload))
        if self.LogIDNew:
            parts.append(_field_bytes(9, self.LogIDNew.encode()))
        return b"".join(parts)


def _parse_header(data: bytes) -> Header:
    header = Header()
    index = 0
    size = len(data)
    while index < size:
        tag, index = _read_varint(data, index)
        number, wire = tag >> 3, tag & 7
        if wire != 2:
            if wire == 0:
                _, index = _read_varint(data, index)
                continue
            if wire == 1:
                index += 8
                continue
            if wire == 5:
                index += 4
                continue
            raise ValueError(f"不支持的 protobuf wire type: {wire}")
        length, index = _read_varint(data, index)
        end = index + length
        if end > size:
            raise ValueError("飞书帧 Header 长度越界")
        value = data[index:end].decode("utf-8", errors="replace")
        index = end
        if number == 1:
            header.key = value
        elif number == 2:
            header.value = value
    return header


def _serialize_header(header: Header) -> bytes:
    parts = []
    if header.key:
        parts.append(_field_bytes(1, header.key.encode()))
    if header.value:
        parts.append(_field_bytes(2, header.value.encode()))
    return b"".join(parts)


def _read_varint(data: bytes, index: int) -> tuple[int, int]:
    result = 0
    shift = 0
    while True:
        if index >= len(data):
            raise ValueError("protobuf varint 截断")
        byte = data[index]
        index += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, index
        shift += 7
        if shift >= 64:
            raise ValueError("protobuf varint 过长")


def _write_varint(value: int) -> bytes:
    value &= 0xFFFFFFFFFFFFFFFF
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def _field_varint(number: int, value: int) -> bytes:
    return _write_varint((number << 3) | 0) + _write_varint(value)


def _field_bytes(number: int, data: bytes) -> bytes:
    return _write_varint((number << 3) | 2) + _write_varint(len(data)) + data
