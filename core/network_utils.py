from curses import window
import socket
import time
import threading
import math
from typing import Optional, TYPE_CHECKING, Callable
from collections import deque
import pexpect
import sys
from typing import cast
from core.utils import build_logger

# Import for type hints only
if TYPE_CHECKING:
    from .ssh_utils import SSHConfig

# Configure logging
logger = build_logger(__name__)

class BufferManager:
    """Manages the buffer size for optimal transfer speed with adaptive adjustment"""
    
    # Default buffer sizes in bytes
    DEFAULT_BUFFER_SIZE = 64 * 1024  # 64KB
    MINIMUM_BUFFER_SIZE = 8 * 1024  # 8KB minimum buffer size
    MAXIMUM_BUFFER_SIZE = 1 * 1024 * 1024  # 1MB maximum buffer size

    @staticmethod
    def accumulate_transfer_stats(func):
        """
        Decorator to accumulate transfer statistics for adaptive buffer adjustment
        
        Args:
            func: Function to wrap
            
        Returns:
            Wrapped function with performance tracking
        """
        import functools
        @functools.wraps(func)
        def wrapper(self, *args, **kwargs):
            # arg[0] is byte_transferred, arg[1] is transfer_time
            if len(args) >= 2:
                bytes_transferred = args[0]
                transfer_time = args[1]
                
                # Update cumulative stats
                self._cumulative_stats['total_bytes'] += bytes_transferred
                self._cumulative_stats['total_time'] += transfer_time
                self._cumulative_stats['call_count'] += 1

                # sliding window
                current_time = time.time()
                window_duration = current_time - self._cumulative_stats['window_start_time']
                if window_duration > 300:
                    # self._reset_cumulative_window()
                    pass
                
            # Call the original function
            return func(self, *args, **kwargs)
        return wrapper
    
    def __init__(self, 
        initial_size: int = DEFAULT_BUFFER_SIZE,
        max_size: int = MAXIMUM_BUFFER_SIZE,
        latency: float = 0.1,
        history_size: int = 10
    ):
        self.buffer_size = initial_size
        self.max_size = max_size
        self.min_size = self.MINIMUM_BUFFER_SIZE
        self.latency = latency  # Network latency in seconds
        self.transfer_history = deque(maxlen=history_size)  # 自动限制大小的循环缓冲区
        self.adjustment_factor = 0.2  # 20% adjustment per iteration
        
        # 性能统计和智能调整相关
        self.performance_stats = {
            'total_transfers': 0,
            'avg_rate_window': deque(maxlen=5),  # 滑动窗口平均
            'last_adjustment_time': time.time(),
            'stability_counter': 0  # 稳定性计数器
        }
        # Cumulative performance data
        self._cumulative_stats = {
            'total_bytes': 0,
            'total_time': 0.0,
            'call_count': 0,
            'window_start_time': time.time()
        }
        
    def measure_initial_bandwidth(self, sock: socket.socket) -> float:
        """
        Measure initial bandwidth by sending test data
        
        Args:
            sock: Socket to test on
            
        Returns:
            Estimated bandwidth in bytes/second
        """
        test_data = b'0' * (32 * 1024)  # 32KB test data
        start_time = time.time()
        
        try:
            sock.sendall(test_data)
            end_time = time.time()
            
            transfer_time = end_time - start_time
            if transfer_time > 0:
                bandwidth = len(test_data) / transfer_time
                logger.info(f"Measured initial bandwidth: {bandwidth / 1024 / 1024:.2f} MB/s")
                return bandwidth
        except Exception as e:
            logger.warning(f"Failed to measure initial bandwidth: {e}")
            
        return 1024 * 1024  # 1MB/s default
        
    def adjust_buffer_size(self, transfer_rate: float) -> int:
        """
        Dynamically adjust buffer size based on network conditions
        
        Args:
            transfer_rate: Current transfer rate in bytes/second
            
        Returns:
            New buffer size in bytes
        """
        # BDP (Bandwidth-Delay Product) calculation using internal latency
        optimal_size = int(transfer_rate * self.latency)
        
        # Apply constraints to keep the buffer size reasonable
        min_size = self.min_size  # 8KB minimum
        max_size = self.max_size  # 1MB maximum

        self.buffer_size = max(min_size, min(optimal_size, max_size))
        logger.info(f"Buffer size adjusted to: {self.buffer_size / 1024:.2f}KB")
        return self.buffer_size
    
    @accumulate_transfer_stats
    def adaptive_adjust(self, bytes_transferred: int, transfer_time: float) -> int:
        """
        改进的自适应调整算法，基于性能趋势智能调整缓冲区大小
        
        Args:
            bytes_transferred: Number of bytes transferred
            transfer_time: Time taken for transfer in seconds
            
        Returns:
            New buffer size in bytes
        """
        if transfer_time <= 0:
            return self.buffer_size
            
        current_time = time.time()
        actual_rate = bytes_transferred / transfer_time
        
        # 存储性能数据
        self.transfer_history.append({
            'rate': actual_rate,
            'time': transfer_time,
            'bytes': bytes_transferred,
            'timestamp': current_time
        })
        self.performance_stats['total_transfers'] += 1
        
        # 防止频繁调整 - 至少间隔1秒
        if current_time - self.performance_stats['last_adjustment_time'] < 1.0:
            return self.buffer_size
        
        # 计算趋势 - 判断性能是在提升还是下降
        trend_factor = self._calculate_performance_trend()
        
        # 基于BDP计算最优大小
        optimal_size = int(actual_rate * self.latency)
        
        # 动态调整因子 - 根据性能趋势调整
        dynamic_factor = self.adjustment_factor * (1 + trend_factor * 0.5)
        dynamic_factor = max(0.1, min(dynamic_factor, 0.4))  # 限制在10%-40%
        
        new_size = int(self.buffer_size * (1 - dynamic_factor) + 
                      optimal_size * dynamic_factor)
        
        # 应用约束并验证缓冲区大小
        self.buffer_size = self.validate_buffer_size(new_size)
        self.performance_stats['last_adjustment_time'] = current_time
        
        logger.debug(f"Smart buffer adjustment: {actual_rate/1024/1024:.2f} MB/s, "
                    f"trend: {trend_factor:.2f}, new buffer: {self.buffer_size/1024:.2f}KB")
        
        return self.buffer_size
    
    def get_buffer_size(self) -> int:
        """Get current buffer size"""
        return self.buffer_size
    
    def get_average_transfer_rate(self) -> float:
        """Get average transfer rate from recent history"""
        if not self._cumulative_stats['call_count']:
            return 1024 * 1024  # 1MB/s default

        total_bytes = self._cumulative_stats['total_bytes']
        total_time = self._cumulative_stats['total_time']

        if total_time > 0:
            return total_bytes / total_time
        return 1024 * 1024

    def set_latency(self, latency: float) -> None:
        """Update the network latency estimate"""
        self.latency = latency
        logger.debug(f"Network latency updated to: {self.latency:.4f}s")

    def _calculate_performance_trend(self) -> float:
        """计算性能趋势：正值表示提升，负值表示下降"""
        if len(self.transfer_history) < 3:
            return 0.0
        
        recent_rates = [h['rate'] for h in list(self.transfer_history)[-3:]]
        if len(recent_rates) >= 2:
            # 简单的线性趋势计算
            trend = (recent_rates[-1] - recent_rates[0]) / recent_rates[0]
            return max(-1.0, min(trend, 1.0))  # 限制在[-1, 1]
        return 0.0
    
    def validate_buffer_size(self, size: int) -> int:
        """验证并修正缓冲区大小"""
        # 确保是2的幂次，提高内存对齐效率
        size = max(self.min_size, min(size, self.max_size))
        
        # 调整为最接近的2的幂次
        power = max(13, min(20, int(math.log2(size))))  # 8KB to 1MB
        validated_size = 2 ** power
        
        return validated_size

    def get_performance_metrics(self) -> dict:
        """获取详细的性能指标"""
        if not self.transfer_history:
            return {
                'current_buffer_kb': self.buffer_size / 1024,
                'average_rate_mbps': 0.0,
                'peak_rate_mbps': 0.0,
                'efficiency_score': 0.0,
                'adjustment_frequency': 0.0,
                'stability_score': 0.0
            }
        
        rates = [h['rate'] for h in self.transfer_history]
        avg_rate = sum(rates) / len(rates)
        peak_rate = max(rates)
        
        # 计算效率分数 (当前速度与峰值速度的比值)
        efficiency = (avg_rate / peak_rate) if peak_rate > 0 else 0.0
        
        # 计算调整频率 (每分钟调整次数)
        time_span = max(1, time.time() - self.performance_stats['last_adjustment_time'])
        adjustment_frequency = (self.performance_stats['total_transfers'] / time_span) * 60
        
        # 计算稳定性分数 (速度变异系数的倒数)
        if len(rates) > 1:
            variance = sum((r - avg_rate) ** 2 for r in rates) / len(rates)
            std_dev = math.sqrt(variance)
            cv = std_dev / avg_rate if avg_rate > 0 else 1.0
            stability = max(0.0, 1.0 - cv)
        else:
            stability = 1.0
        
        return {
            'current_buffer_kb': self.buffer_size / 1024,
            'average_rate_mbps': avg_rate / (1024 * 1024),
            'peak_rate_mbps': peak_rate / (1024 * 1024),
            'efficiency_score': efficiency,
            'adjustment_frequency': adjustment_frequency,
            'stability_score': stability
        }
    
    def reset_performance_stats(self):
        """重置性能统计数据"""
        self.transfer_history.clear()
        self.performance_stats.update({
            'total_transfers': 0,
            'last_adjustment_time': time.time(),
            'stability_counter': 0
        })
        self.performance_stats['avg_rate_window'].clear()
        logger.debug("Buffer manager performance stats reset")
    
    def suggest_optimal_settings(self) -> dict:
        """基于历史数据建议最优设置"""
        if len(self.transfer_history) < 5:
            return {
                'suggested_buffer_size': self.buffer_size,
                'confidence': 'low',
                'reason': 'Insufficient data for optimization'
            }
        
        # 分析不同缓冲区大小的性能
        performance_data = {}
        for entry in self.transfer_history:
            # 这里假设我们在transfer_history中存储了buffer_size信息
            # 在实际实现中，可能需要修改数据结构
            rate = entry['rate']
            if 'buffer_size' in entry:
                buffer_size = entry['buffer_size']
                if buffer_size not in performance_data:
                    performance_data[buffer_size] = []
                performance_data[buffer_size].append(rate)
        
        if not performance_data:
            # 基于当前性能趋势建议
            trend = self._calculate_performance_trend()
            if trend > 0.1:  # 性能提升趋势
                suggested_size = min(self.buffer_size * 1.2, self.max_size)
                reason = "Performance trending upward, suggest larger buffer"
            elif trend < -0.1:  # 性能下降趋势
                suggested_size = max(self.buffer_size * 0.8, self.min_size)
                reason = "Performance trending downward, suggest smaller buffer"
            else:
                suggested_size = self.buffer_size
                reason = "Performance stable, maintain current buffer size"
        else:
            # 找到平均性能最好的缓冲区大小
            best_buffer = max(performance_data.keys(), 
                            key=lambda x: sum(performance_data[x]) / len(performance_data[x]))
            suggested_size = best_buffer
            reason = f"Historical data shows best performance with {best_buffer/1024:.1f}KB buffer"
        
        return {
            'suggested_buffer_size': int(suggested_size),
            'confidence': 'high' if len(self.transfer_history) >= 10 else 'medium',
            'reason': reason,
            'current_buffer_size': self.buffer_size
        }

class NetworkMonitor:
    """Monitors network conditions to optimize transfer"""
    
    def __init__(self, target_host: str, ssh_config: Optional['SSHConfig'] = None):
        self.target_host = target_host
        self.ssh_config = ssh_config
        self.latency = 0.1  # Initial default latency estimate (100ms)
        self.monitoring_enabled = False
        self.monitoring_thread = None
        self.latency_history = deque(maxlen=20)
        self.quality_change_callback = None

    def measure_latency_with_ssh(self) -> float:
        """Measure network latency using SSH connection timing"""
        if not self.ssh_config:
            logger.warning("Cannot measure latency with SSH: No SSH configuration provided")
            return self.latency
            
        try:
            start_time = time.time()
            
            # Construct SSH command to just return immediately
            cmd = self.ssh_config.get_ssh_command_base()
            cmd.extend([
                "-o", "ConnectTimeout=5",
                f"{self.ssh_config.jump_user}@{self.ssh_config.jump_server}",
                "echo", "connected"
            ])
            
            # Convert to string for pexpect
            cmd_str = " ".join(cmd)
            child = pexpect.spawn(cmd_str, timeout=10)
            
            # Handle password if needed
            if self.ssh_config.use_password and self.ssh_config.password:
                child.expect('password:')
                child.sendline(self.ssh_config.password)
                
            # Wait for command completion
            child.expect(pexpect.EOF)
            end_time = time.time()
            
            # Calculate latency
            self.latency = (end_time - start_time)
            logger.info(f"SSH latency: {self.latency:.4f}s")
            return self.latency
            
        except Exception as e:
            logger.warning(f"Failed to measure latency with SSH: {e}")
            return self.latency

    def measure_latency_with_socket(self, port: int = 22, attempts: int = 5) -> float:
        """改进的Socket延迟测量"""
        latency_values = []
        successful_attempts = 0
        
        for attempt in range(attempts):
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(3)  # 减少超时时间
            
            try:
                start_time = time.perf_counter()  # 使用更精确的计时器
                result = sock.connect_ex((self.target_host, port))
                end_time = time.perf_counter()
                
                if result == 0:  # 连接成功
                    latency_values.append(end_time - start_time)
                    successful_attempts += 1
                else:
                    logger.debug(f"Connection attempt {attempt+1} failed with code: {result}")
                    
            except Exception as e:
                logger.debug(f"Socket latency measurement attempt {attempt+1} failed: {e}")
            finally:
                sock.close()
                
            # 防止过于频繁的连接尝试
            if attempt < attempts - 1:
                time.sleep(0.1)
        
        if latency_values:
            # 去除异常值，使用中位数
            latency_values.sort()
            if len(latency_values) >= 3:
                # 去掉最高和最低值
                trimmed_values = latency_values[1:-1]
                self.latency = sum(trimmed_values) / len(trimmed_values)
            else:
                self.latency = sum(latency_values) / len(latency_values)
                
            logger.info(f"Socket latency measured: {self.latency*1000:.2f}ms "
                       f"({successful_attempts}/{attempts} successful)")
        else:
            logger.warning("All latency measurement attempts failed, using default")
            
        return self.latency

    def measure_latency(self) -> float:
        """
        Measure network latency to the target host using direct ping
        
        Returns:
            Latency in seconds
        """
        if 0: #self.ssh_config:
            # May need to input password so it will affect the latency
            return self.measure_latency_with_ssh()
        else:
            return self.measure_latency_with_socket()
    
    def estimate_bandwidth(self, data_size: int, transfer_time: float) -> float:
        """
        Estimate bandwidth based on actual transfer metrics
        
        Args:
            data_size: Size of transferred data in bytes
            transfer_time: Time taken to transfer in seconds
            
        Returns:
            Estimated bandwidth in bytes/second
        """
        if transfer_time > 0:
            return data_size / transfer_time
        return 1024 * 1024  # Default 1MB/s if can't calculate

    def assess_network_quality(self) -> dict:
        """评估网络质量"""
        quality_metrics = {
            'latency_ms': self.latency * 1000,
            'quality_score': 0,
            'recommended_buffer_size': 64 * 1024,
            'quality_description': 'Unknown'
        }
        
        latency_ms = self.latency * 1000
        
        if latency_ms < 20:
            quality_metrics.update({
                'quality_score': 95,
                'recommended_buffer_size': 128 * 1024,
                'quality_description': 'Excellent (LAN/High-speed)'
            })
        elif latency_ms < 50:
            quality_metrics.update({
                'quality_score': 80,
                'recommended_buffer_size': 96 * 1024,
                'quality_description': 'Good (Broadband)'
            })
        elif latency_ms < 150:
            quality_metrics.update({
                'quality_score': 60,
                'recommended_buffer_size': 64 * 1024,
                'quality_description': 'Fair (DSL/Cable)'
            })
        else:
            quality_metrics.update({
                'quality_score': 30,
                'recommended_buffer_size': 32 * 1024,
                'quality_description': 'Poor (Satellite/Mobile)'
            })
        
        return quality_metrics

    def start_continuous_monitoring(self, interval: float = 30.0, callback: Optional[Callable] = None) -> None:
        """启动连续网络监控"""
        self.monitoring_enabled = True
        self.quality_change_callback = callback
        
        def monitor_loop():
            while self.monitoring_enabled:
                try:
                    old_latency = self.latency
                    new_latency = self.measure_latency()
                    
                    self.latency_history.append(new_latency)
                    
                    # 检测显著的延迟变化（超过30%）
                    if old_latency > 0 and abs(new_latency - old_latency) / old_latency > 0.3:
                        if self.quality_change_callback:
                            self.quality_change_callback(old_latency, new_latency)
                            
                    time.sleep(interval)
                except Exception as e:
                    logger.error(f"Network monitoring error: {e}")
                    time.sleep(interval)
        
        self.monitoring_thread = threading.Thread(target=monitor_loop, daemon=True)
        self.monitoring_thread.start()
        logger.info(f"Started continuous network monitoring (interval: {interval}s)")

    def stop_monitoring(self) -> None:
        """停止网络监控"""
        self.monitoring_enabled = False
        if self.monitoring_thread and self.monitoring_thread.is_alive():
            self.monitoring_thread.join(timeout=5.0)
        logger.info("Network monitoring stopped")

    def get_latency_statistics(self) -> dict:
        """获取延迟统计信息"""
        if not self.latency_history:
            return {
                'current': self.latency,
                'min': self.latency,
                'max': self.latency,
                'avg': self.latency,
                'std_dev': 0.0,
                'sample_count': 0
            }
        
        latencies = list(self.latency_history)
        avg_latency = sum(latencies) / len(latencies)
        variance = sum((l - avg_latency) ** 2 for l in latencies) / len(latencies)
        std_dev = math.sqrt(variance)
        
        return {
            'current': self.latency,
            'min': min(latencies),
            'max': max(latencies),
            'avg': avg_latency,
            'std_dev': std_dev,
            'sample_count': len(latencies)
        }

    def is_network_stable(self, threshold: float = 0.2) -> bool:
        """判断网络是否稳定（延迟变化小于阈值）"""
        if len(self.latency_history) < 3:
            return True
        
        stats = self.get_latency_statistics()
        if stats['avg'] > 0:
            coefficient_of_variation = stats['std_dev'] / stats['avg']
            return coefficient_of_variation < threshold
        return True

def create_optimized_buffer_manager(target_host: str, ssh_config: Optional['SSHConfig'] = None) -> tuple:
    """创建优化的缓冲区管理器和网络监控器"""
    
    # 创建网络监控器并测量初始延迟
    monitor = NetworkMonitor(target_host, ssh_config)
    initial_latency = monitor.measure_latency()
    
    # 评估网络质量
    quality = monitor.assess_network_quality()
    
    # 基于网络质量创建缓冲区管理器
    buffer_manager = BufferManager(
        initial_size=quality['recommended_buffer_size'],
        latency=initial_latency
    )
    
    logger.info(f"Network assessment: {quality['quality_description']} "
               f"(latency: {quality['latency_ms']:.1f}ms, "
               f"buffer: {quality['recommended_buffer_size']/1024}KB)")
    
    return buffer_manager, monitor

# Additional utility functions for performance optimization
class AdaptiveTransferOptimizer:
    """自适应传输优化器，结合BufferManager和NetworkMonitor的功能"""
    
    def __init__(self, target_host: str, ssh_config: Optional['SSHConfig'] = None):
        self.buffer_manager, self.network_monitor = create_optimized_buffer_manager(target_host, ssh_config)
        self.transfer_stats = {
            'total_bytes': 0,
            'total_time': 0.0,
            'transfer_count': 0,
            'last_optimization_time': time.time()
        }
        
    def optimize_for_transfer(self, estimated_size: int) -> dict:
        """根据传输大小优化参数"""
        # 获取当前网络质量
        quality = self.network_monitor.assess_network_quality()
        
        # 根据文件大小调整策略
        if estimated_size < 10 * 1024 * 1024:  # 小于10MB，使用较小缓冲区
            buffer_factor = 0.5
        elif estimated_size < 100 * 1024 * 1024:  # 10-100MB，标准缓冲区
            buffer_factor = 1.0
        else:  # 大于100MB，使用较大缓冲区
            buffer_factor = 1.5
            
        optimized_buffer = int(quality['recommended_buffer_size'] * buffer_factor)
        optimized_buffer = self.buffer_manager.validate_buffer_size(optimized_buffer)
        
        return {
            'buffer_size': optimized_buffer,
            'network_quality': quality,
            'recommended_strategy': self._get_transfer_strategy(quality['quality_score'])
        }
    
    def _get_transfer_strategy(self, quality_score: int) -> str:
        """根据网络质量推荐传输策略"""
        if quality_score >= 80:
            return "aggressive"  # 激进模式，大缓冲区，高并发
        elif quality_score >= 60:
            return "balanced"   # 平衡模式
        else:
            return "conservative"  # 保守模式，小缓冲区，低并发
    
    def update_transfer_stats(self, bytes_transferred: int, transfer_time: float):
        """更新传输统计"""
        self.transfer_stats['total_bytes'] += bytes_transferred
        self.transfer_stats['total_time'] += transfer_time
        self.transfer_stats['transfer_count'] += 1
        
        # 更新缓冲区管理器
        self.buffer_manager.adaptive_adjust(bytes_transferred, transfer_time)
        
        # 定期更新网络延迟（每60秒）
        current_time = time.time()
        if current_time - self.transfer_stats['last_optimization_time'] > 60:
            new_latency = self.network_monitor.measure_latency()
            self.buffer_manager.set_latency(new_latency)
            self.transfer_stats['last_optimization_time'] = current_time
    
    def get_performance_summary(self) -> dict:
        """获取性能摘要"""
        if self.transfer_stats['total_time'] > 0:
            avg_speed = self.transfer_stats['total_bytes'] / self.transfer_stats['total_time']
        else:
            avg_speed = 0
            
        return {
            'total_bytes': self.transfer_stats['total_bytes'],
            'total_time': self.transfer_stats['total_time'],
            'transfer_count': self.transfer_stats['transfer_count'],
            'average_speed_mbps': avg_speed / (1024 * 1024),
            'current_buffer_size_kb': self.buffer_manager.get_buffer_size() / 1024,
            'network_latency_ms': self.network_monitor.latency * 1000,
            'network_stable': self.network_monitor.is_network_stable()
        }
