#!/usr/bin/env python3
"""
Optimized Protocol Handler Module

简化版的协议处理器，减少冗余代码，提高可读性和性能。
"""
from typing import Tuple, Union, Optional, Protocol as TypingProtocol, runtime_checkable
from core.utils import build_logger

logger = build_logger(__name__)

@runtime_checkable
class ReadableStream(TypingProtocol):
    """流读取接口协议"""
    def read(self, __n: int = -1, /) -> bytes: ...

class OptimizedProtocolHandler:
    """
    优化的协议处理器 - 简化设计，提高性能
    """
    # 协议常量
    MSG_TYPE = "MSG"
    FILE_TYPE = "FILE"
    DELIMITER = "|"
    HEADER_SIZE = 8  # 简化为8字节固定头部长度

    @classmethod
    def encode_data(cls, data_type: str, payload: Union[str, bytes]) -> bytes:
        """
        编码数据，使用更简洁的协议格式
        
        协议格式: [8字节头部长度][头部内容][载荷数据]
        头部内容: "TYPE|SIZE"
        """
        # 统一转换为bytes
        payload_bytes = payload.encode('utf-8') if isinstance(payload, str) else payload
        
        # 构建头部
        header_content = f"{data_type}{cls.DELIMITER}{len(payload_bytes)}"
        header_bytes = header_content.encode('utf-8')
        
        # 8字节固定长度的头部长度字段
        header_len_bytes = f"{len(header_bytes):08d}".encode('utf-8')
        
        return header_len_bytes + header_bytes + payload_bytes

    @classmethod
    def decode_from_stream(cls, stream: ReadableStream, buffer_size: int = 64*1024) -> Optional[Tuple[str, bytes]]:
        """
        从流中解码完整的消息，合并头部和载荷解析
        
        Returns:
            Tuple of (data_type, payload_bytes) or None if failed
        """
        try:
            # 1. 读取固定8字节的头部长度
            header_len_data = stream.read(cls.HEADER_SIZE)
            if len(header_len_data) != cls.HEADER_SIZE:
                logger.debug(f"Stream ended while reading header length")
                return None
            
            header_len = int(header_len_data.decode('utf-8'))
            
            # 2. 读取头部内容
            header_data = stream.read(header_len)
            if len(header_data) != header_len:
                logger.debug(f"Stream ended while reading header content")
                return None
            
            # 3. 解析头部获取类型和载荷大小
            header_content = header_data.decode('utf-8')
            data_type, size_str = header_content.split(cls.DELIMITER, 1)
            payload_size = int(size_str)
            
            # 4. 读取载荷数据（分块读取以支持大文件）
            payload_chunks = []
            bytes_read = 0
            
            while bytes_read < payload_size:
                chunk_size = min(buffer_size, payload_size - bytes_read)
                chunk = stream.read(chunk_size)
                if not chunk:
                    logger.error(f"Stream ended while reading payload. Expected {payload_size}, got {bytes_read}")
                    return None
                payload_chunks.append(chunk)
                bytes_read += len(chunk)
            
            payload_bytes = b''.join(payload_chunks)
            logger.debug(f"Successfully decoded {data_type} message with {len(payload_bytes)} bytes payload")
            
            return data_type, payload_bytes
            
        except (ValueError, UnicodeDecodeError) as e:
            logger.error(f"Protocol decoding error: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error during decoding: {e}")
            return None

    @staticmethod
    def decode_message(payload_bytes: bytes) -> Optional[str]:
        """解码消息载荷为字符串"""
        try:
            return payload_bytes.decode('utf-8')
        except UnicodeDecodeError as e:
            logger.error(f"Failed to decode message payload: {e}")
            return None

if __name__ == '__main__':
    # 测试代码
    import io
    
    handler = OptimizedProtocolHandler()
    
    # 测试消息编码解码
    msg = "Hello, optimized protocol!"
    encoded = handler.encode_data(handler.MSG_TYPE, msg)
    print(f"Encoded message: {len(encoded)} bytes")
    
    # 解码测试
    stream = io.BytesIO(encoded)
    result = handler.decode_from_stream(stream)
    if result:
        data_type, payload = result
        if data_type == handler.MSG_TYPE:
            decoded_msg = handler.decode_message(payload)
            print(f"Decoded message: {decoded_msg}")
    
    # 测试文件数据
    file_data = b"Binary file content here..."
    encoded_file = handler.encode_data(handler.FILE_TYPE, file_data)
    print(f"Encoded file: {len(encoded_file)} bytes")
    
    stream = io.BytesIO(encoded_file)
    result = handler.decode_from_stream(stream)
    if result:
        data_type, payload = result
        print(f"Decoded file: type={data_type}, size={len(payload)}")
