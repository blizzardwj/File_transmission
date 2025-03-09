import os
from pathlib import Path
import time
import statistics

def benchmark_os_path(file_path: str, iterations: int = 1000):
    times = []
    for _ in range(iterations):
        start = time.perf_counter_ns()
        size = os.path.getsize(file_path)
        end = time.perf_counter_ns()
        times.append(end - start)
    return statistics.mean(times), statistics.stdev(times)

def benchmark_path(file_path: str, iterations: int = 1000):
    times = []
    p = Path(file_path)
    for _ in range(iterations):
        start = time.perf_counter_ns()
        size = p.stat().st_size
        end = time.perf_counter_ns()
        times.append(end - start)
    return statistics.mean(times), statistics.stdev(times)

def run_benchmark(file_size: int, iterations: int = 1000):
    """Run benchmark with specified file size"""
    test_file = f"test_file_{file_size}b.bin"
    
    # Create test file
    with open(test_file, "wb") as f:
        f.write(os.urandom(file_size))
    
    try:
        print(f"\nTesting with {file_size/1024/1024:.2f}MB file...")
        
        # Warm up the filesystem cache
        _ = os.path.getsize(test_file)
        _ = Path(test_file).stat().st_size
        
        # Run benchmarks
        os_mean, os_std = benchmark_os_path(test_file, iterations)
        path_mean, path_std = benchmark_path(test_file, iterations)
        
        print(f"\nResults for {file_size/1024/1024:.2f}MB file (time in microseconds):")
        print(f"os.path.getsize():   Mean={os_mean/1000:.2f} µs  Std={os_std/1000:.2f} µs")
        print(f"Path.stat().st_size: Mean={path_mean/1000:.2f} µs  Std={path_std/1000:.2f} µs")
        print(f"Ratio: Path.stat().st_size is {path_mean/os_mean:.2f}x slower")
        
    finally:
        # Clean up test file
        if os.path.exists(test_file):
            os.remove(test_file)

def main():
    print("Benchmarking file size operations...\n")
    print("Running 1000 iterations for each test...\n")
    
    # Test with different file sizes
    sizes = [
        1024 * 1024,     # 1MB
        10 * 1024 * 1024,  # 10MB
        100 * 1024 * 1024, # 100MB
    ]
    
    for size in sizes:
        run_benchmark(size)

if __name__ == "__main__":
    main()
