import os
import stat
import json
import time
import socket
import paramiko
from tqdm.auto import tqdm
import traceback
from ipywidgets import widgets, HBox, VBox, Layout
from IPython.display import display, clear_output


# 3. SFTP进度显示类
class ProgressTracker:
    def __init__(self):
        self.progress_bar = None
        self.last_bytes = 0
        self.start_time = None
        self.current_file = None
        
    def init_progress_bar(self, filename, total_size):
        """初始化一个新的进度条"""
        self.current_file = filename
        self.progress_bar = tqdm(
            total=total_size,
            unit='B',
            unit_scale=True,
            desc=f"传输 {os.path.basename(filename)}",
            mininterval=0.5
        )
        self.last_bytes = 0
        self.start_time = time.time()
        
    def update_progress(self, transferred_bytes, total_bytes):
        """更新进度条"""
        if self.progress_bar:
            increment = transferred_bytes - self.last_bytes
            self.progress_bar.update(increment)
            self.last_bytes = transferred_bytes
            
    def finish_progress(self):
        """完成进度条"""
        if self.progress_bar:
            self.progress_bar.close()
            elapsed = time.time() - self.start_time
            return elapsed
        return 0

# 4. 文件传输核心类
class SecureFileTransfer:
    def __init__(self):
        self.ssh = None
        self.sftp = None
        self.tracker = ProgressTracker()
        self.output = None
        
    def _connect(self, jump_server, port, user, password):
        """建立SSH连接并初始化SFTP"""
        try:
            self.ssh = paramiko.SSHClient()
            self.ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            self.ssh.connect(
                hostname=jump_server,
                port=int(port),
                username=user,
                password=password,
                timeout=10
            )
            self.sftp = self.ssh.open_sftp()
            return True
        except Exception as e:
            self._show_error(f"连接失败: {str(e)}")
            return False

    def _upload_file(self, local_path, remote_path):
        """上传单个文件，支持断点续传"""
        filename = os.path.basename(local_path)
        file_size = os.path.getsize(local_path)
        
        # 检查远程文件是否存在
        try:
            remote_size = self.sftp.stat(remote_path).st_size
            if remote_size == file_size:
                print(f"✅ 文件 {filename} 已存在且大小一致，跳过上传")
                return True
            if remote_size < file_size:
                print(f"🔄 发现未完成的传输，从 {remote_size}/{file_size} 字节处继续上传...")
                resume_position = remote_size
            else:
                print(f"⚠️ 远程文件大小 ({remote_size} 字节) 大于本地文件 ({file_size} 字节)，重新上传...")
                resume_position = 0
        except IOError:
            # 远程文件不存在
            resume_position = 0
        
        try:
            # 初始化进度条
            self.tracker.init_progress_bar(local_path, file_size)
            
            # 打开本地和远程文件
            with open(local_path, 'rb') as local_file:
                if resume_position > 0:
                    local_file.seek(resume_position)
                
                # 使用SFTP的putfo方法
                remote_file = self.sftp.file(
                    remote_path,
                    mode='ab' if resume_position > 0 else 'wb'
                )
                
                # 开始传输
                self.tracker.last_bytes = resume_position
                buffer_size = 32768  # 32KB 缓冲区
                
                data = local_file.read(buffer_size)
                while data:
                    remote_file.write(data)
                    transferred = resume_position + remote_file.tell()
                    self.tracker.update_progress(transferred, file_size)
                    data = local_file.read(buffer_size)
                
                remote_file.close()
                
            elapsed = self.tracker.finish_progress()
            print(f"✅ 文件 {filename} 上传完成，耗时 {elapsed:.2f} 秒")
            return True
        except Exception as e:
            self._show_error(f"上传文件 {filename} 失败: {str(e)}")
            self.tracker.finish_progress()
            return False

    def _download_file(self, remote_path, local_path):
        """下载单个文件，支持断点续传"""
        filename = os.path.basename(remote_path)
        
        # 获取远程文件大小
        try:
            file_size = self.sftp.stat(remote_path).st_size
        except Exception as e:
            self._show_error(f"获取远程文件 {filename} 大小失败: {str(e)}")
            return False
        
        # 检查本地文件是否存在
        if os.path.exists(local_path):
            local_size = os.path.getsize(local_path)
            if local_size == file_size:
                print(f"✅ 文件 {filename} 已存在且大小一致，跳过下载")
                return True
            if local_size < file_size:
                print(f"🔄 发现未完成的传输，从 {local_size}/{file_size} 字节处继续下载...")
                resume_position = local_size
            else:
                print(f"⚠️ 本地文件大小 ({local_size} 字节) 大于远程文件 ({file_size} 字节)，重新下载...")
                resume_position = 0
        else:
            # 确保本地目录存在
            local_dir = os.path.dirname(local_path)
            if local_dir and not os.path.exists(local_dir):
                try:
                    os.makedirs(local_dir)
                except Exception as e:
                    self._show_error(f"创建目录 {local_dir} 失败: {str(e)}")
                    return False
            resume_position = 0
        
        try:
            # 初始化进度条
            self.tracker.init_progress_bar(remote_path, file_size)
            
            # 打开远程和本地文件
            remote_file = self.sftp.file(remote_path, 'rb')
            if resume_position > 0:
                remote_file.seek(resume_position)
            
            with open(local_path, 'ab' if resume_position > 0 else 'wb') as local_file:
                # 开始传输
                self.tracker.last_bytes = resume_position
                buffer_size = 32768  # 32KB 缓冲区
                
                data = remote_file.read(buffer_size)
                while data:
                    local_file.write(data)
                    transferred = resume_position + (remote_file.tell() - resume_position)
                    self.tracker.update_progress(transferred, file_size)
                    data = remote_file.read(buffer_size)
            
            remote_file.close()
            
            elapsed = self.tracker.finish_progress()
            print(f"✅ 文件 {filename} 下载完成，耗时 {elapsed:.2f} 秒")
            return True
        except Exception as e:
            self._show_error(f"下载文件 {filename} 失败: {str(e)}")
            self.tracker.finish_progress()
            return False

    def _upload_directory(self, local_dir, remote_dir):
        """递归上传目录"""
        # 确保远程目录存在
        try:
            self.sftp.stat(remote_dir)
        except IOError:
            print(f"🗂️ 创建远程目录: {remote_dir}")
            self.sftp.mkdir(remote_dir)
        
        # 遍历本地目录
        success = True
        for item in os.listdir(local_dir):
            local_path = os.path.join(local_dir, item)
            remote_path = os.path.join(remote_dir, item)
            
            if os.path.isfile(local_path):
                if not self._upload_file(local_path, remote_path):
                    success = False
            elif os.path.isdir(local_path):
                if not self._upload_directory(local_path, remote_path):
                    success = False
        
        return success

    def _download_directory(self, remote_dir, local_dir):
        """递归下载目录"""
        # 确保本地目录存在
        if not os.path.exists(local_dir):
            try:
                os.makedirs(local_dir)
                print(f"🗂️ 创建本地目录: {local_dir}")
            except Exception as e:
                self._show_error(f"创建目录 {local_dir} 失败: {str(e)}")
                return False
        
        # 遍历远程目录
        success = True
        try:
            for item in self.sftp.listdir_attr(remote_dir):
                remote_path = os.path.join(remote_dir, item.filename)
                local_path = os.path.join(local_dir, item.filename)
                
                if stat.S_ISDIR(item.st_mode):
                    if not self._download_directory(remote_path, local_path):
                        success = False
                else:
                    if not self._download_file(remote_path, local_path):
                        success = False
        except Exception as e:
            self._show_error(f"读取远程目录 {remote_dir} 失败: {str(e)}")
            return False
        
        return success

    def _transfer(self, src, dst, operation):
        """执行文件传输主逻辑"""
        if operation == 'send':
            if os.path.isfile(src):
                print(f"📤 上传文件: {src} -> {dst}")
                return self._upload_file(src, dst)
            elif os.path.isdir(src):
                print(f"📤 上传目录: {src} -> {dst}")
                return self._upload_directory(src, dst)
            else:
                self._show_error(f"本地路径 {src} 不存在或无法访问")
                return False
        else:  # receive
            try:
                stat_info = self.sftp.stat(src)
                if stat.S_ISDIR(stat_info.st_mode):
                    print(f"📥 下载目录: {src} -> {dst}")
                    return self._download_directory(src, dst)
                else:
                    # 如果目标是目录，则在目录中创建同名文件
                    if os.path.exists(dst) and os.path.isdir(dst):
                        dst = os.path.join(dst, os.path.basename(src))
                    print(f"📥 下载文件: {src} -> {dst}")
                    return self._download_file(src, dst)
            except Exception as e:
                self._show_error(f"检查远程路径 {src} 失败: {str(e)}")
                return False

    def _show_error(self, msg):
        """显示错误信息"""
        with self.output:
            clear_output()
            print(f"❌ {msg}")

    def run_transfer(self, params):
        """执行传输主逻辑"""
        self.output = widgets.Output()
        display(self.output)
        
        with self.output:
            # 验证参数有效性
            if not all([params['jump_server'], params['user'], params['password']]):
                self._show_error("请填写所有必填参数")
                return

            # 建立连接
            print("🔄 正在连接跳板机...")
            if not self._connect(params['jump_server'], params['port'], 
                              params['user'], params['password']):
                return

            try:
                # 执行传输
                print("🚀 开始传输...")
                success = self._transfer(
                    src=params['local_path'],
                    dst=params['remote_path'],
                    operation=params['operation']
                )

                if success:
                    print("✅ 传输成功完成！")
                else:
                    self._show_error("传输未完成")

            except Exception as e:
                self._show_error(f"意外错误: {traceback.format_exc()}")
            finally:
                if self.sftp:
                    self.sftp.close()
                if self.ssh:
                    self.ssh.close()
                print("🔒 连接已关闭")