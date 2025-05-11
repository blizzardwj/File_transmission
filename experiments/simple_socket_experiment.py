#!/usr/bin/env python3
"""
简单Socket网络编程实验

这个脚本演示了Python中socket编程的基础知识，包括：
1. 创建TCP服务器
2. 创建TCP客户端
3. 发送和接收数据
4. 处理多个客户端连接

用法：
    # 运行服务器
    python simple_socket_experiment.py --mode server [--host HOST] [--port PORT]
    
    # 运行客户端
    python simple_socket_experiment.py --mode client [--host HOST] [--port PORT] [--message "自定义消息"]
    
示例：
    # 终端1: 启动服务器
    python simple_socket_experiment.py --mode server
    
    # 终端2: 运行客户端发送消息
    python simple_socket_experiment.py --mode client --message "你好，服务器！"
"""

import socket
import argparse
import threading
from pathlib import Path
from core.utils import load_config, build_logger


# 配置日志
logger = build_logger(__name__)

def run_server(host: str = 'localhost', port: int = 8000):
    """
    运行一个简单的TCP服务器
    
    Args:
        host: 服务器主机名或IP地址
        port: 服务器监听端口
    """
    # 步骤1: 创建服务器套接字
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    
    # 步骤2: 设置套接字选项(允许地址重用，这样服务器重启时不会出现"地址已在使用"错误)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    
    try:
        # 步骤3: 将套接字绑定到指定地址和端口
        server_socket.bind((host, port))
        
        # 步骤4: 开始监听连接请求(参数5表示最大等待连接数)
        server_socket.listen(5)
        logger.info(f"服务器正在 {host}:{port} 监听")
        
        # 步骤5: 循环接受客户端连接
        while True:
            # 接受连接请求
            client_socket, client_address = server_socket.accept()
            logger.info(f"接受来自 {client_address} 的连接")
            
            # 为每个客户端创建一个新线程处理通信
            client_thread = threading.Thread(
                target=handle_client,
                args=(client_socket, client_address),
                daemon=True
            )
            client_thread.start()
            
    except KeyboardInterrupt:
        logger.info("服务器被用户停止")
    except Exception as e:
        logger.error(f"服务器错误: {e}")
    finally:
        # 确保服务器套接字被关闭
        if server_socket:
            server_socket.close()
            logger.info("服务器套接字已关闭")

def handle_client(client_socket, client_address):
    """
    处理与单个客户端的通信
    
    Args:
        client_socket: 与客户端通信的套接字
        client_address: 客户端地址
    """
    try:
        # 发送欢迎消息给客户端
        welcome_message = "欢迎连接到Python Socket服务器！输入'exit'退出。"
        client_socket.sendall(welcome_message.encode('utf-8'))
        
        # 接收和回复客户端消息
        while True:
            # 接收客户端消息
            data = client_socket.recv(1024)
            if not data:
                logger.info(f"客户端 {client_address} 关闭了连接")
                break
                
            # 解码并处理消息
            message = data.decode('utf-8').strip()
            logger.info(f"从 {client_address} 收到: {message}")
            
            # 如果客户端发送'exit'，则结束连接
            if message.lower() == 'exit':
                client_socket.sendall("连接关闭。再见！".encode('utf-8'))
                break
                
            # 处理并发送回复
            response = f"服务器回复: 你发送了 '{message}' ({len(message)} 字节)"
            client_socket.sendall(response.encode('utf-8'))
            
    except Exception as e:
        logger.error(f"处理客户端 {client_address} 时出错: {e}")
    finally:
        # 关闭客户端套接字
        client_socket.close()
        logger.info(f"与客户端 {client_address} 的连接已关闭")

def run_client(host: str = 'localhost', port: int = 8000, message: str = None):
    """
    运行一个简单的TCP客户端
    
    Args:
        host: 服务器主机名或IP地址
        port: 服务器端口
        message: 要发送的消息(如果为None，则进入交互模式)
    """
    # 步骤1: 创建客户端套接字
    client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    
    try:
        # 步骤2: 连接到服务器
        logger.info(f"尝试连接到 {host}:{port}...")
        client_socket.connect((host, port))
        logger.info("已连接到服务器")
        
        # 步骤3: 接收服务器的欢迎消息
        welcome_data = client_socket.recv(1024)
        welcome_message = welcome_data.decode('utf-8')
        logger.info(f"服务器: {welcome_message}")
        
        # 步骤4: 根据运行模式发送消息
        if message:
            # 发送单条消息然后退出
            logger.info(f"发送消息: {message}")
            client_socket.sendall(message.encode('utf-8'))
            
            # 接收并显示服务器响应
            response_data = client_socket.recv(1024)
            response = response_data.decode('utf-8')
            logger.info(f"服务器响应: {response}")
            
            # 发送退出命令
            logger.info("发送退出命令")
            client_socket.sendall("exit".encode('utf-8'))
            
            # 接收最终响应
            final_data = client_socket.recv(1024)
            final_message = final_data.decode('utf-8')
            logger.info(f"服务器: {final_message}")
        else:
            # 交互模式
            logger.info("进入交互模式。输入消息发送给服务器。输入'exit'退出。")
            
            while True:
                # 获取用户输入
                user_input = input("> ")
                
                # 发送用户输入到服务器
                client_socket.sendall(user_input.encode('utf-8'))
                
                # 如果用户输入'exit'，则退出循环
                if user_input.lower() == 'exit':
                    # 接收服务器的最终响应
                    final_data = client_socket.recv(1024)
                    final_message = final_data.decode('utf-8')
                    logger.info(f"服务器: {final_message}")
                    break
                    
                # 接收并显示服务器响应
                response_data = client_socket.recv(1024)
                response = response_data.decode('utf-8')
                logger.info(f"服务器响应: {response}")
                
    except ConnectionRefusedError:
        logger.error(f"连接被拒绝。请确保服务器在 {host}:{port} 上运行")
    except Exception as e:
        logger.error(f"客户端错误: {e}")
    finally:
        # 关闭客户端套接字
        client_socket.close()
        logger.info("客户端套接字已关闭")

def main():
    """主函数"""
    # 在脚本全局加载配置
    config_path = Path(__file__).parent / "config.yaml"
    cfg = load_config(config_path)

    parser = argparse.ArgumentParser(description="简单的Socket编程实验")
    parser.add_argument("--mode", choices=["server", "client"], required=True,
                        help="运行模式: server(服务器) 或 client(客户端)")
    parser.add_argument("--host", default=None,
                        help="主机名或IP地址 (默认从配置文件加载)")
    parser.add_argument("--port", type=int, default=None,
                        help="端口号 (默认从配置文件加载)")
    parser.add_argument("--message",
                        default=None,
                        help="客户端模式下要发送的消息 (默认从配置文件加载)")
    
    args = parser.parse_args()
    
    # 从配置文件加载默认值
    host = args.host or cfg[args.mode]['host']
    port = args.port or cfg[args.mode]['port']
    logger.info(f"server address: {host}:{port}")

    if args.mode == 'client':
        message = args.message or cfg['client'].get('message')
        logger.info(f"client message: {message}")
    # 启动服务或客户端
    if args.mode == "server":
        run_server(host, port)
    else:  # client mode
        run_client(host, port, message)

if __name__ == "__main__":
    main()
