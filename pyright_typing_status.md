# Pyright Typing Status - backup-main Branch

## Overview
This branch contains work to add strict pyright typing to the pyspr codebase. The project has pyright configured in strict mode (`typeCheckingMode = "strict"`), requiring comprehensive type annotations throughout.

## Current Status: 124 errors remaining

### Completed Work

#### 1. Core Module Typing (fake_pygithub.py)
- **Return type annotations** added for all major methods:
  - `create_pull() -> 'FakePullRequest'`
  - `get_user() -> Optional['FakeNamedUser']`
  - `get_repo() -> 'FakeRepository'`
  - `get_pull() -> Optional['FakePullRequest']`
  - `requestJsonAndCheck() -> Tuple[Dict[str, Any], Dict[str, Any]]`
  
- **Maybe/Ensure pattern** implemented for optional fields:
  ```python
  maybe_github_ref: Any = field(default=None, repr=False)
  
  @property
  def github_ref(self) -> 'FakeGithub':
      return ensure(self.maybe_github_ref)
  ```
  This pattern provides type-safe access to optional fields that are guaranteed to be set after initialization.

- **Local variable annotations** added throughout:
  - `requested_users: List['FakeNamedUser'] = []`
  - `pr_nodes: List[Dict[str, Any]] = []`
  - `pr_items: List[Tuple[str, FakePullRequest]] = []`
  - `max_pr_numbers: Dict[str, int] = {}`

- **Type casts** added where necessary:
  - Used `cast()` in `requestJsonAndCheck()` and `_handle_graphql()` to ensure correct return types

#### 2. Circular Import Resolution
- Moved `ensure()` function from `pyspr/github/__init__.py` to `pyspr/util.py`
- Created local `TypeVar` in `util.py` to avoid circular dependencies

#### 3. Code Cleanup
- Renamed `_data` to `data_record` throughout `FakePullRequest` for clarity
- Fixed method calls from `_save_state()` to `save_state()` and `_load_state()` to `load_state()`
- Renamed test file to follow pytest convention: `fake_pygithub_test.py` → `test_fake_pygithub.py`

### Remaining Issues by Category

#### 1. Protocol Incompatibilities (2 errors)
**File**: `mock_setup.py`
- `FakeGithub` doesn't fully implement `PyGithubProtocol`:
  - `get_repo` method signature mismatch
  - Return type `FakeRepository` not compatible with `GitHubRepoProtocol`
- `Github` (real PyGithub) also incompatible with `PyGithubProtocol`:
  - `get_repo` has additional `lazy: bool = False` parameter
  - `get_user` uses `Opt[str]` instead of `str | None`

#### 2. Test File Type Issues (115+ errors)
**File**: `test_fake_pygithub.py`
- Unknown types for most variables and method calls
- Missing type inference for:
  - `github = FakeGithub()`
  - `repo = github.get_repo()`
  - `pr = repo.create_pull()`
  - All attributes and methods on these objects

**File**: `test_e2e.py`
- Optional member access without None checks:
  - `base_ref` accessed on potentially None object
  - `commit` accessed on potentially None object

**File**: `test_helpers.py`
- Missing parameter type annotations for helper functions
- Unknown return types for decorated functions

#### 3. Import and Usage Issues (7 errors)
**File**: `fixtures.py`
- Unused imports: `Optional`, `run_cmd`
- Unknown types for `node` variables in test fixtures

### Error Distribution by File
- `test_fake_pygithub.py`: ~90 errors (mostly unknown types)
- `test_helpers.py`: ~20 errors (missing annotations)
- `fixtures.py`: 6 errors (unused imports, unknown types)
- `test_e2e.py`: 3 errors (optional member access)
- `mock_setup.py`: 2 errors (protocol incompatibility)

## Suggested Next Steps

### Phase 1: Fix Protocol Compatibility (High Priority)
1. **Update PyGithubProtocol** to match actual PyGithub API:
   - Add optional parameters to method signatures
   - Consider using `Protocol` with `@runtime_checkable` for better flexibility
   - May need to create adapter classes or update the protocol definition

2. **Update GitHubRepoProtocol** to match both `Repository` and `FakeRepository`:
   - Ensure all required methods are present
   - Check parameter and return type compatibility

### Phase 2: Add Test File Annotations (Medium Priority)
1. **Start with test_fake_pygithub.py**:
   - Add type annotations for all test variables
   - Import necessary types from fake_pygithub module
   - Consider creating a test-specific type stub if needed

2. **Fix test_helpers.py**:
   - Add parameter and return type annotations to all helper functions
   - Properly type the decorator functions

3. **Clean up fixtures.py**:
   - Remove unused imports
   - Add proper types for node variables (likely need to import from fake_pygithub)

### Phase 3: Handle Optional Access (Low Priority)
1. **Add None checks in test_e2e.py**:
   - Check if PR exists before accessing `base_ref` and `commit`
   - Use `assert pr is not None` or conditional checks

### Phase 4: Improve Type Coverage
1. **Consider adding py.typed marker** once all errors are resolved
2. **Add type stubs** for any third-party dependencies if needed
3. **Enable additional pyright checks** if desired

## Testing Strategy
- Run `rye run pyright` after each phase to verify progress
- Run `rye run pytest -vsx` to ensure no runtime regressions
- Consider adding type checking to CI pipeline once clean

## Notes
- The codebase uses a direct port approach from Go (ejoffe/spr), so type annotations should maintain algorithm compatibility
- The fake PyGithub implementation is used for testing without hitting the real GitHub API
- Type safety is particularly important for the protocol interfaces between real and fake implementations