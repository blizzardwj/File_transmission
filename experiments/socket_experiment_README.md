# Python Socket 网络编程实验

## 概述

这个实验旨在通过简单的代码示例来展示Python中Socket编程的基础知识。通过运行这个实验，您可以了解如何建立TCP连接、发送和接收数据、处理多个客户端等基本概念。

## Socket 编程基础概念

### 什么是Socket？

Socket（套接字）是一种允许不同计算机上的进程进行通信的技术。它提供了一个双向通信机制，可以在网络中的计算机之间传输数据。

### 主要概念

1. **地址族**：定义通信使用的协议（如IPv4、IPv6等）
   - `AF_INET`: 表示使用IPv4协议
   - `AF_INET6`: 表示使用IPv6协议

2. **套接字类型**：定义传输方式
   - `SOCK_STREAM`: 用于TCP连接（可靠、面向连接）
   - `SOCK_DGRAM`: 用于UDP连接（不可靠、无连接）

3. **服务器-客户端模型**：最常见的通信模式
   - 服务器：创建套接字、绑定到特定地址和端口、监听和接受连接
   - 客户端：创建套接字、连接到服务器地址和端口、发送/接收数据

## 代码结构

`simple_socket_experiment.py` 包含两个主要部分：

1. **服务器部分**：
   - 创建套接字
   - 绑定到指定地址和端口
   - 监听连接请求
   - 接受连接并创建新线程处理客户端
   - 与客户端通信

2. **客户端部分**：
   - 创建套接字
   - 连接到服务器
   - 发送数据
   - 接收响应
   - 关闭连接

## 运行实验

### 实验设备准备

实验可以运行在一台设备上，在两个 terminal 中分别运行服务器和客户端。

### 服务器模式

```bash
python simple_socket_experiment.py --mode server [--host HOST] [--port PORT]
```

参数说明：
- `--host`: 服务器主机名或IP地址（默认: localhost）
- `--port`: 服务器端口号（默认: 8000）

示例：
```bash
python simple_socket_experiment.py --mode server --port 9000
```

### 客户端模式

```bash
python simple_socket_experiment.py --mode client [--host HOST] [--port PORT] [--message "自定义消息"]
```

参数说明：
- `--host`: 服务器主机名或IP地址（默认: localhost）
- `--port`: 服务器端口号（默认: 8000）
- `--message`: 要发送的消息（如果不提供，则进入交互模式）

示例（非交互式）：
```bash
python simple_socket_experiment.py --mode client --message "你好，服务器！"
```

示例（交互式）：
```bash
python simple_socket_experiment.py --mode client
```

## 多客户端测试

要测试服务器如何处理多个客户端连接，请遵循以下步骤：

1. 在一个终端中启动服务器：
   ```bash
   python simple_socket_experiment.py --mode server
   ```

2. 在多个不同的终端窗口中运行客户端：
   ```bash
   python simple_socket_experiment.py --mode client
   ```

3. 观察服务器如何同时处理多个客户端的连接和通信。

## 代码分析

### 服务器工作流程

1. 创建套接字对象并设置选项
2. 绑定到指定地址和端口
3. 开始监听连接请求
4. 当接收到连接请求时，创建新线程处理客户端
5. 在线程中与客户端通信，直到客户端关闭连接

### 客户端工作流程

1. 创建套接字对象
2. 连接到服务器
3. 发送数据并接收响应
4. 关闭连接

## 注意事项

- 这个实验使用TCP协议，它是面向连接的可靠协议
- 服务器使用多线程处理多个客户端，这是一种常见模式但不是唯一的方法（其他方法包括多进程、异步I/O等）
- 默认情况下，套接字操作是阻塞的，这意味着程序会在读取/写入操作时等待
- 实际开发中，您可能需要考虑错误处理、超时机制、安全性等更多因素

## 扩展实验

尝试以下扩展实验以深入了解Socket编程：

1. 修改代码支持UDP协议（使用`SOCK_DGRAM`套接字类型）
2. 实现一个简单的聊天室，允许多个客户端互相通信
3. 添加数据加密功能
4. 实现文件传输功能
5. 使用`select`或`asyncio`模块实现非阻塞I/O操作

## 相关资源

- [Python Socket编程官方文档](https://docs.python.org/3/library/socket.html)
- [Python 网络编程教程](https://realpython.com/python-sockets/)
- [计算机网络基础](https://www.geeksforgeeks.org/computer-network-tutorials/)
