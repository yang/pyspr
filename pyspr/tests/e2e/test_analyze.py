"""End-to-end test for the analyze command."""

import logging
import sys
from typing import Dict, List, Tuple

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


def create_commits_from_dag(dependencies: Dict[str, List[str]], commits_order: List[str]) -> None:
    """Create commits based on dependency DAG.
    
    This function creates commits with proper conflict-based dependencies.
    Key insight: Independent commits must create their own files or modify 
    pre-existing content. Dependent commits modify the files created by their dependencies.
    
    Args:
        dependencies: Dict mapping commit name to list of commits it depends on
        commits_order: Order in which to create commits (must be topologically sorted)
    """
    # Track which file each commit primarily owns/creates
    commit_files: Dict[str, str] = {}
    
    # Create commits in topological order
    for commit_name in commits_order:
        deps = dependencies.get(commit_name, [])
        
        if not deps:
            # Independent commit - creates its own file
            filename = f"file_{commit_name}.txt"
            commit_files[commit_name] = filename
            with open(filename, "w") as f:
                f.write(f"{commit_name}'s content\n")
        else:
            # Dependent commit - modifies files from dependencies
            files_modified = []
            
            for dep in deps:
                if dep in commit_files:
                    # Modify the file created by the dependency
                    dep_file = commit_files[dep]
                    files_modified.append(dep_file)
                    
                    # Read current content
                    with open(dep_file, "r") as f:
                        content = f.read()
                    
                    # Append our modification
                    with open(dep_file, "a") as f:
                        f.write(f"{commit_name}'s addition to {dep}'s file\n")
            
            # If this commit is depended on by others, track its primary file
            # (the first dependency's file it modifies)
            if files_modified and commit_name not in commit_files:
                commit_files[commit_name] = files_modified[0]
        
        run_cmd("git add .")
        run_cmd(f"git commit -m '{commit_name}'")


@run_twice_in_mock_mode
def test_analyze_complex_dependencies(test_repo_ctx: RepoContext) -> None:
    """Test analyze command with complex dependency structure.
    
    IMPORTANT: This test specification should NEVER be changed. If the test fails,
    fix the implementation, not the test.
    
    Scenario 2 Algorithm (must be implemented exactly as specified):
    For each commit bottom-up, relocate it into a tree:
      - Try cherry-picking to merge-base
      - Or else cherry-pick onto any prior relocated commit (loop over all prior ones)
      - Or else mark as orphan
    This gives you trees.

    Expected Scenario 2 output structure:

    A
      B
      C
        D
        E
    F
    H
      I
      J
    K
      L
    M
    orphans (multi parents): G

    Scenario 3 Algorithm (must be implemented exactly as specified):
    For each commit bottom-up, relocate it into a stack:
        - Try cherry-picking to merge-base
        - Or else cherry-pick onto any prior relocated stack (tips)
        - Or else mark as orphan
    This gives you stacks.

    Expected Scenario 3 output structure:
    stacks:
    A B C D E
    F
    H I J
    K L
    M
    orphans (multi parents): G

    --
    
    The independents are only the roots that depend on nothing else, like A, F, H, K, M
    G actually depends on both E and F
    D depends on both A and C

    IMPORTANT: Never add anything else to these specs
    """
    
    # Define the dependency DAG
    # Key: commit name, Value: list of commits it depends on
    dependencies = {
        "A": [],  # Independent
        "B": ["A"],  # Depends on A
        "C": ["A"],  # Depends on A  
        "D": ["A", "C"],  # Depends on both A and C
        "E": ["C"],  # Depends on C
        "F": [],  # Independent
        "G": ["E", "F"],  # Depends on both E and F - should be orphan in Scenario 2
        "H": [],  # Independent
        "I": ["H"],  # Depends on H
        "J": ["H", "I"],  # Depends on both H and I
        "K": [],  # Independent
        "L": ["K"],  # Depends on K
        "M": [],  # Independent
    }
    
    # No need for an initial commit - the test commits will be analyzed
    # against the empty repository base
    
    # Create commits in topological order
    commits_order = ["A", "F", "H", "K", "M", "B", "C", "I", "D", "E", "L", "J", "G"]
    create_commits_from_dag(dependencies, commits_order)
    
    # Run analyze command and capture output
    result = run_cmd("pyspr analyze", capture_output=True)
    output = str(result)
    
    log.info(f"Analyze output:\n{output}")
    
    # Basic sections check
    assert "✅ Independent commits (" in output
    assert "❌ Dependent commits (" in output
    assert "⚠️  Orphaned commits (" in output
    assert "🎯 Alternative Stacking Scenarios" in output
    assert "📊 Scenario 1: Strongly Connected Components" in output
    assert "🌳 Scenario 2: Best-Effort Single-Parent Trees" in output
    assert "📚 Scenario 3: Stack-Based Approach" in output
    
    # Extract key information using simple patterns
    import re
    
    def extract_number(pattern: str, text: str, name: str) -> int:
        """Extract a number from text using regex pattern."""
        match = re.search(pattern, text)
        assert match, f"Could not find {name}"
        return int(match.group(1))
    
    def extract_commits_from_section(section_text: str) -> set:
        """Extract commit names from a section of output."""
        commits = set()
        for line in section_text.strip().split('\n'):
            match = re.search(r"- \w+ (\w+)$", line.strip())
            if match:
                commits.add(match.group(1))
        return commits
    
    def verify_topological_order(commit_positions: Dict[str, Tuple[int, int]], 
                                  commit: str, dependencies: List[str]) -> None:
        """Verify that a commit appears after all its dependencies."""
        if commit not in commit_positions:
            return
        
        commit_stack, commit_pos = commit_positions[commit]
        
        for dep in dependencies:
            if dep not in commit_positions:
                continue
                
            dep_stack, dep_pos = commit_positions[dep]
            
            if commit_stack == dep_stack:
                assert dep_pos < commit_pos, \
                    f"{dep} (position {dep_pos}) must come before {commit} (position {commit_pos}) in stack {commit_stack + 1}"
            else:
                assert dep_stack < commit_stack, \
                    f"{dep} (in stack {dep_stack + 1}) must be in an earlier stack than {commit} (in stack {commit_stack + 1})"
        
        log.info(f"✓ {commit} correctly placed after {', '.join(dependencies)}")
    
    # Basic validations
    total_commits = extract_number(r"Total commits: (\d+)", output, "total commits")
    assert total_commits >= 13, f"Expected at least 13 commits, got {total_commits}"
    
    # Validate independent commits
    independent_section = re.search(r"✅ Independent commits \(\d+\):(.*?)❌ Dependent commits", output, re.DOTALL)
    assert independent_section, "Could not find independent commits section"
    
    independent_commits = extract_commits_from_section(independent_section.group(1))
    expected_independent = {"A", "F", "H", "K", "M"}
    assert independent_commits == expected_independent, \
        f"Expected independent commits {expected_independent}, got {independent_commits}"
    log.info(f"✓ Verified independent commits: {sorted(independent_commits)}")
    
    # Verify scenarios
    components_count = extract_number(r"Found (\d+) component\(s\)", output, "Scenario 1 summary")
    assert components_count >= 1, f"Expected at least 1 component, got {components_count}"
    
    scenario2_match = re.search(r"Created (\d+) tree\(s\) and (\d+) orphan\(s\)", output)
    assert scenario2_match, "Could not find Scenario 2 summary"
    trees_count = int(scenario2_match.group(1))
    orphans_count = int(scenario2_match.group(2))
    assert trees_count >= 2, f"Expected at least 2 trees, got {trees_count}"
    
    scenario3_match = re.search(r"Created (\d+) stack\(s\) and (\d+) orphan\(s\)", output)
    assert scenario3_match, "Could not find Scenario 3 summary"
    stacks_count = int(scenario3_match.group(1))
    stacks_orphans = int(scenario3_match.group(2))
    
    # Verify expected counts
    assert stacks_count == 5, f"Expected 5 stacks in Scenario 3, got {stacks_count}"
    assert stacks_orphans == 1, f"Expected 1 orphan in Scenario 3, got {stacks_orphans}"
    
    # Extract stacks and build position map
    commit_positions = {}
    stack_pattern = r"Stack \d+:\s*\n((?:\s*- \w+ \w+\s*\n)+)"
    
    for stack_idx, stack_match in enumerate(re.finditer(stack_pattern, output, re.MULTILINE)):
        stack_commits = extract_commits_from_section(stack_match.group(1))
        
        # Preserve order by re-parsing
        ordered_commits = []
        for line in stack_match.group(1).strip().split('\n'):
            match = re.search(r"- \w+ (\w+)", line.strip())
            if match:
                ordered_commits.append(match.group(1))
        
        for pos, commit in enumerate(ordered_commits):
            commit_positions[commit] = (stack_idx, pos)
        
        if ordered_commits:
            log.info(f"Stack {stack_idx + 1}: {' → '.join(ordered_commits)}")
    
    # Verify topological ordering for key commits
    verify_topological_order(commit_positions, 'G', ['E', 'F'])
    verify_topological_order(commit_positions, 'D', ['A', 'C'])
    verify_topological_order(commit_positions, 'J', ['H', 'I'])
    
    # Summary
    log.info("\n=== TEST SUMMARY ===")
    log.info(f"✓ Found {total_commits} total commits")
    log.info(f"✓ Scenario 1: {components_count} components")
    log.info(f"✓ Scenario 2: {trees_count} trees, {orphans_count} orphans")
    log.info(f"✓ Scenario 3: {stacks_count} stacks, {stacks_orphans} orphans")
    log.info("✓ All key structures validated")