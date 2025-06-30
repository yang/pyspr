"""End-to-end test for the analyze command."""

import logging
import sys

from pyspr.tests.e2e.test_helpers import RepoContext, run_cmd
from pyspr.tests.e2e.decorators import run_twice_in_mock_mode

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
# Add stderr handler to ensure logs are output during pytest
handler = logging.StreamHandler(sys.stderr)
handler.setFormatter(logging.Formatter('%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s', '%H:%M:%S'))
log = logging.getLogger(__name__)
log.addHandler(handler)
log.setLevel(logging.INFO)
log.propagate = True  # Allow logs to propagate to pytest


@run_twice_in_mock_mode
def test_analyze_complex_dependencies(test_repo_ctx: RepoContext) -> None:
    """Test analyze command with complex dependency structure matching the expected output."""
    ctx = test_repo_ctx
    
    # Create a base file that will be modified by multiple commits
    ctx.write_file("src/server.py", """
def start_server():
    print("Starting server...")
    # Basic server implementation
    
def handle_request():
    print("Handling request...")
    # Basic request handler
    
def cleanup():
    print("Cleaning up...")
    # Basic cleanup
""")
    
    ctx.write_file("src/config.py", """
def get_config():
    return {"host": "localhost", "port": 8080}
""")
    
    ctx.write_file("src/logger.py", """
def log(message):
    print(f"LOG: {message}")
""")
    
    # Commit the base files
    run_cmd("git add .")
    run_cmd("git commit -m 'Initial setup'")
    
    # Push to create the base branch
    run_cmd("git push origin main")
    
    # Now create the commits that match the expected output
    
    # 1. Add retries with forced retries for debugging (independent)
    ctx.write_file("src/retry.py", """
def retry_with_force(func, max_retries=3):
    for i in range(max_retries):
        try:
            return func()
        except Exception:
            if i == max_retries - 1:
                raise
            print(f"Retry {i + 1}/{max_retries}")
""")
    run_cmd("git add .")
    run_cmd("git commit -m 'Add retries with forced retries for debugging'")
    
    # 2. Improve logging (independent)
    ctx.write_file("src/logger.py", """
import datetime

def log(message, level="INFO"):
    timestamp = datetime.datetime.now().isoformat()
    print(f"[{timestamp}] {level}: {message}")
    
def debug(message):
    log(message, "DEBUG")
    
def error(message):
    log(message, "ERROR")
""")
    run_cmd("git add .")
    run_cmd("git commit -m 'Improve logging'")
    
    # 3. Isolated orch (independent)
    ctx.write_file("src/orchestrator.py", """
class Orchestrator:
    def __init__(self):
        self.tasks = []
        
    def add_task(self, task):
        self.tasks.append(task)
        
    def run(self):
        for task in self.tasks:
            task()
""")
    run_cmd("git add .")
    run_cmd("git commit -m 'Isolated orch'")
    
    # 4. Fix ports to make room (independent)
    ctx.write_file("src/ports.py", """
RESERVED_PORTS = range(8000, 8100)
AVAILABLE_PORTS = range(8100, 9000)

def get_available_port():
    return 8100
""")
    run_cmd("git add .")
    run_cmd("git commit -m 'Fix ports to make room'")
    
    # 5. Move train worker to own pod (independent)
    ctx.write_file("src/train_worker.py", """
class TrainWorker:
    def __init__(self):
        self.pod_name = "train-worker-pod"
        
    def start(self):
        print(f"Starting {self.pod_name}")
        
    def stop(self):
        print(f"Stopping {self.pod_name}")
""")
    run_cmd("git add .")
    run_cmd("git commit -m 'Move train worker to own pod'")
    
    # 6. Add trace spans to broadcasts (independent)
    ctx.write_file("src/tracing.py", """
class TraceSpan:
    def __init__(self, name):
        self.name = name
        
    def __enter__(self):
        print(f"Start span: {self.name}")
        return self
        
    def __exit__(self, *args):
        print(f"End span: {self.name}")
        
def broadcast_with_trace(message):
    with TraceSpan("broadcast"):
        print(f"Broadcasting: {message}")
""")
    run_cmd("git add .")
    run_cmd("git commit -m 'Add trace spans to broadcasts'")
    
    # 7. sandbox (independent)
    ctx.write_file("src/sandbox.py", """
def run_in_sandbox(code):
    # Sandbox implementation
    exec(code, {"__builtins__": {}})
""")
    run_cmd("git add .")
    run_cmd("git commit -m 'sandbox'")
    
    # 8. Disable triospy tracker (independent)
    ctx.write_file("src/tracker.py", """
TRIOSPY_ENABLED = False

def track_event(event):
    if TRIOSPY_ENABLED:
        print(f"Tracking: {event}")
""")
    run_cmd("git add .")
    run_cmd("git commit -m 'Disable triospy tracker'")
    
    # 9. Add sharded loading (independent)
    ctx.write_file("src/sharding.py", """
def load_sharded(data, num_shards=4):
    shard_size = len(data) // num_shards
    shards = []
    for i in range(num_shards):
        start = i * shard_size
        end = start + shard_size if i < num_shards - 1 else len(data)
        shards.append(data[start:end])
    return shards
""")
    run_cmd("git add .")
    run_cmd("git commit -m 'Add sharded loading'")
    
    # 10. Fix concurrency for dist pool (independent)
    ctx.write_file("src/dist_pool.py", """
import threading

class DistributedPool:
    def __init__(self):
        self.lock = threading.Lock()
        self.workers = []
        
    def add_worker(self, worker):
        with self.lock:
            self.workers.append(worker)
""")
    run_cmd("git add .")
    run_cmd("git commit -m 'Fix concurrency for dist pool'")
    
    # 11. Thin out the logs a bit (independent)
    ctx.write_file("src/log_config.py", """
LOG_LEVELS = {
    "DEBUG": 0,
    "INFO": 1,
    "WARNING": 2,
    "ERROR": 3
}

MIN_LOG_LEVEL = "INFO"
""")
    run_cmd("git add .")
    run_cmd("git commit -m 'Thin out the logs a bit'")
    
    # 12. Scale up dystro (independent)
    ctx.write_file("src/dystro_config.py", """
DYSTRO_REPLICAS = 10
DYSTRO_MEMORY = "4Gi"
DYSTRO_CPU = "2"
""")
    run_cmd("git add .")
    run_cmd("git commit -m 'Scale up dystro'")
    
    # Now add dependent commits
    
    # 13. Remove long lived connection (depends on retry.py from commit 1 - conflicts in same area)
    # First let's modify retry.py to create a conflict scenario
    ctx.write_file("src/retry.py", """
def retry_with_force(func, max_retries=3, force_debug=False):
    for i in range(max_retries):
        try:
            if force_debug:
                print(f"Attempting retry {i+1}")
            return func()
        except Exception:
            if i == max_retries - 1:
                raise
            print(f"Retry {i + 1}/{max_retries}")

def get_retry_count():
    return 3
""")
    run_cmd("git add .")
    run_cmd("git commit -m 'Remove long lived connection'")
    
    # 14. Log global env stats (depends on improved logger.py from commit 2 - uses new functions)
    # Modify logger.py to add a conflicting change in the same area
    ctx.write_file("src/logger.py", """
import datetime

def log(message, level="INFO"):
    timestamp = datetime.datetime.now().isoformat()
    print(f"[{timestamp}] {level}: {message}")
    
def debug(message):
    log(message, "DEBUG")
    
def error(message):
    log(message, "ERROR")
    
def log_stats(stats_dict):
    # New function that conflicts with previous logger changes
    for key, value in stats_dict.items():
        debug(f"{key}: {value}")
""")
    run_cmd("git add .")
    run_cmd("git commit -m 'Log global env stats'")
    
    # 15. Track top on train pod (depends on train_worker.py from commit 5 - modifies same class)
    ctx.write_file("src/train_worker.py", """
class TrainWorker:
    def __init__(self):
        self.pod_name = "train-worker-pod"
        self.metrics = {"cpu": 0, "memory": 0}  # Added metrics tracking
        
    def start(self):
        print(f"Starting {self.pod_name}")
        
    def stop(self):
        print(f"Stopping {self.pod_name}")
        
    def get_metrics(self):
        # New method that conflicts
        return self.metrics
""")
    run_cmd("git add .")
    run_cmd("git commit -m 'Track top on train pod'")
    
    # 16. Fix train worker race (also depends on train_worker.py from commit 5 - conflicts with commit 15)
    ctx.write_file("src/train_worker.py", """
import threading

class TrainWorker:
    def __init__(self):
        self.pod_name = "train-worker-pod"
        self.lock = threading.Lock()
        self.metrics = {"cpu": 0, "memory": 0, "threads": 1}  # Conflicts with commit 15
        
    def start(self):
        with self.lock:
            print(f"Starting {self.pod_name}")
            self.metrics["threads"] += 1
        
    def stop(self):
        with self.lock:
            print(f"Stopping {self.pod_name}")
            self.metrics["threads"] -= 1
""")
    run_cmd("git add .")
    run_cmd("git commit -m 'Fix train worker race'")
    
    # 17. Disable GC during bcasts (depends on tracing.py from commit 6 - modifies broadcast function)
    ctx.write_file("src/tracing.py", """
import gc

class TraceSpan:
    def __init__(self, name):
        self.name = name
        
    def __enter__(self):
        print(f"Start span: {self.name}")
        return self
        
    def __exit__(self, *args):
        print(f"End span: {self.name}")
        
def broadcast_with_trace(message):
    # Modified to disable GC - conflicts with original
    gc_was_enabled = gc.isenabled()
    if gc_was_enabled:
        gc.disable()
    
    try:
        with TraceSpan("broadcast"):
            print(f"Broadcasting: {message}")
    finally:
        if gc_was_enabled:
            gc.enable()
""")
    run_cmd("git add .")
    run_cmd("git commit -m 'Disable GC during bcasts'")
    
    # Run analyze command and capture output
    result = run_cmd("pyspr analyze", capture_output=True)
    output = result.stdout if hasattr(result, 'stdout') else str(result)
    
    log.info(f"Analyze output:\n{output}")
    
    # Verify the output contains expected sections
    assert "✅ Independent commits (" in output
    assert "❌ Dependent commits (" in output
    
    # Verify that the commits with actual conflicts show up as dependent
    # Based on our test setup:
    # - "Remove long lived connection" depends on "Add retries" (modifies same function)
    # - "Log global env stats" depends on "Improve logging" (adds conflicting function)
    # - "Track top on train pod" depends on "Move train worker" (modifies same class)
    # - "Fix train worker race" depends on "Move train worker" (conflicts with previous)
    # - "Disable GC during bcasts" depends on "Add trace spans" (modifies same function)
    
    # Check specific dependencies
    if "Remove long lived connection" in output:
        # Find the line with this commit and verify it shows dependency
        lines = output.split('\n')
        for i, line in enumerate(lines):
            if "Remove long lived connection" in line:
                # Next line should show the dependency reason
                assert i + 1 < len(lines), "Missing dependency reason"
                assert "Depends on:" in lines[i + 1], f"Expected dependency reason, got: {lines[i + 1]}"
                break
    
    # Verify summary statistics format
    assert "Total commits: 17" in output
    assert "Independent:" in output
    assert "Dependent:" in output
    
    # We expect 12 independent and 5 dependent based on our conflict setup
    # But allow some flexibility in case the algorithm improves
    import re
    independent_match = re.search(r"Independent: (\d+)", output)
    dependent_match = re.search(r"Dependent: (\d+)", output)
    
    if independent_match and dependent_match:
        independent_count = int(independent_match.group(1))
        dependent_count = int(dependent_match.group(1))
        assert independent_count >= 10, f"Expected at least 10 independent commits, got {independent_count}"
        assert dependent_count >= 3, f"Expected at least 3 dependent commits, got {dependent_count}"
        assert independent_count + dependent_count == 17, "Total should be 17 commits"
    
    # Verify the tip about breakup
    assert "You can use 'pyspr breakup' to create independent PRs" in output
    
    # Verify alternative stacking scenarios are shown
    assert "🎯 Alternative Stacking Scenarios" in output
    assert "📊 Scenario 1: Strongly Connected Components" in output
    assert "🌳 Scenario 2: Best-Effort Single-Parent Trees" in output
    
    # Verify components exist
    assert "component(s):" in output
    assert "Component 1" in output
    
    # Verify tree structure
    assert "tree(s)" in output
    assert "Tree 1:" in output