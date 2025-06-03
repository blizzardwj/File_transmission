#!/usr/bin/env python3
"""
Optimized Socket Transport Module

简化版的Socket传输层，减少冗余代码，提高可读性和性能。
"""
import socket
import threading
from typing import Optional, Tuple, Callable
from core.utils import build_logger
from core.optimized_protocol_handler import ReadableStream

logger = build_logger(__name__)

class OptimizedSocketTransport:
    """
    优化的Socket传输层 - 简化设计，专注核心功能
    """
    DEFAULT_BUFFER_SIZE = 64 * 1024

    def __init__(self, buffer_size: int = DEFAULT_BUFFER_SIZE):
        self.buffer_size = buffer_size
        self.sock: Optional[socket.socket] = None
        self.sock_file: Optional[socket.SocketIO] = None
        self.server_socket: Optional[socket.socket] = None
        self.running = False
        logger.debug(f"SocketTransport initialized with buffer size: {buffer_size}")

    def connect(self, host: str, port: int, timeout: float = 10.0) -> bool:
        """连接到远程服务器"""
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.settimeout(timeout)
            self.sock.connect((host, port))
            
            # 创建文件对象用于流式读取
            self.sock_file = self.sock.makefile('rb', buffering=0)
            
            logger.info(f"Connected to {host}:{port}")
            return True
            
        except (socket.timeout, socket.error) as e:
            logger.error(f"Failed to connect to {host}:{port}: {e}")
            self._cleanup_client()
            return False

    def send_all(self, data: bytes) -> bool:
        """发送所有数据"""
        if not self.sock:
            logger.error("Not connected")
            return False
        
        try:
            self.sock.sendall(data)  # 使用sendall确保所有数据发送完毕
            logger.debug(f"Sent {len(data)} bytes")
            return True
        except socket.error as e:
            logger.error(f"Send failed: {e}")
            return False

    def get_readable_stream(self) -> Optional[ReadableStream]:
        """获取可读流对象"""
        if not self.sock_file:
            logger.error("No readable stream available")
            return None
        return self.sock_file

    def start_server(self, host: str, port: int, 
                    client_handler: Callable[['OptimizedSocketTransport', Tuple], None]) -> bool:
        """启动服务器"""
        try:
            self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.server_socket.bind((host, port))
            self.server_socket.listen(5)
            self.running = True
            
            logger.info(f"Server listening on {host}:{port}")
            
            while self.running:
                try:
                    client_sock, addr = self.server_socket.accept()
                    logger.info(f"Client connected: {addr}")
                    
                    # 为每个客户端创建独立的传输实例
                    client_transport = OptimizedSocketTransport(self.buffer_size)
                    client_transport.sock = client_sock
                    client_transport.sock_file = client_sock.makefile('rb', buffering=0)
                    
                    # 在新线程中处理客户端
                    thread = threading.Thread(
                        target=self._handle_client_wrapper,
                        args=(client_handler, client_transport, addr),
                        daemon=True
                    )
                    thread.start()
                    
                except socket.error as e:
                    if self.running:
                        logger.error(f"Accept error: {e}")
                    break
                    
            return True
            
        except Exception as e:
            logger.error(f"Server start failed: {e}")
            self._cleanup_server()
            return False

    def _handle_client_wrapper(self, handler: Callable, client_transport: 'OptimizedSocketTransport', addr: Tuple):
        """客户端处理包装器，确保资源清理"""
        try:
            handler(client_transport, addr)
        except Exception as e:
            logger.error(f"Client handler error for {addr}: {e}")
        finally:
            client_transport.close()

    def stop_server(self):
        """停止服务器"""
        self.running = False
        if self.server_socket:
            try:
                self.server_socket.close()
                logger.info("Server stopped")
            except Exception as e:
                logger.debug(f"Server cleanup error: {e}")
            finally:
                self.server_socket = None

    def close(self):
        """关闭连接"""
        self._cleanup_client()
        if self.server_socket:
            self.stop_server()

    def _cleanup_client(self):
        """清理客户端连接"""
        if self.sock_file:
            try:
                self.sock_file.close()
            except Exception as e:
                logger.debug(f"Socket file cleanup error: {e}")
            finally:
                self.sock_file = None
                
        if self.sock:
            try:
                self.sock.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass  # 可能已经关闭
            try:
                self.sock.close()
            except Exception as e:
                logger.debug(f"Socket cleanup error: {e}")
            finally:
                self.sock = None
                logger.info("Connection closed")

    def _cleanup_server(self):
        """清理服务器资源"""
        self.running = False
        if self.server_socket:
            try:
                self.server_socket.close()
            except Exception as e:
                logger.debug(f"Server socket cleanup error: {e}")
            finally:
                self.server_socket = None

if __name__ == '__main__':
    # 简化的测试代码
    def echo_handler(transport: OptimizedSocketTransport, addr: Tuple):
        """简单的回显处理器"""
        from core.optimized_protocol_handler import OptimizedProtocolHandler
        
        logger.info(f"Handling client: {addr}")
        stream = transport.get_readable_stream()
        if not stream:
            return
            
        try:
            # 使用协议处理器解码消息
            result = OptimizedProtocolHandler.decode_from_stream(stream)
            if result:
                data_type, payload = result
                logger.info(f"Received {data_type} message: {len(payload)} bytes")
                
                # 回显消息
                response = OptimizedProtocolHandler.encode_data(data_type, b"ECHO: " + payload)
                transport.send_all(response)
            else:
                logger.warning(f"Failed to decode message from {addr}")
                
        except Exception as e:
            logger.error(f"Handler error for {addr}: {e}")

    # 测试服务器
    server = OptimizedSocketTransport()
    server_thread = threading.Thread(
        target=server.start_server,
        args=("127.0.0.1", 9006, echo_handler),
        daemon=True
    )
    server_thread.start()
    print("Test server started on port 9006")

    # 测试客户端
    import time
    from core.optimized_protocol_handler import OptimizedProtocolHandler
    
    time.sleep(0.1)  # 等待服务器启动
    
    client = OptimizedSocketTransport()
    if client.connect("127.0.0.1", 9006):
        # 发送测试消息
        msg = OptimizedProtocolHandler.encode_data(
            OptimizedProtocolHandler.MSG_TYPE, 
            "Hello from optimized client!"
        )
        client.send_all(msg)
        
        # 接收回复
        stream = client.get_readable_stream()
        if stream:
            result = OptimizedProtocolHandler.decode_from_stream(stream)
            if result:
                data_type, payload = result
                response = OptimizedProtocolHandler.decode_message(payload)
                print(f"Received response: {response}")
        
        client.close()
    
    server.stop_server()
    print("Test completed")
