"""Unit tests for git module, specifically duplicate commit-id detection."""

import pytest
from pyspr.typing import Commit, DuplicateCommitIDError
from pyspr.git import check_for_duplicate_commit_ids, parse_local_commit_stack


class TestDuplicateCommitIDDetection:
    """Tests for duplicate commit-id detection."""

    def test_no_duplicates_passes(self) -> None:
        """Test that a stack with unique commit-ids passes validation."""
        commits = [
            Commit.from_strings("abc12345", "aaa" * 13 + "a", "First commit"),
            Commit.from_strings("def67890", "bbb" * 13 + "b", "Second commit"),
            Commit.from_strings("12345678", "ccc" * 13 + "c", "Third commit"),
        ]
        # Should not raise
        check_for_duplicate_commit_ids(commits)

    def test_empty_stack_passes(self) -> None:
        """Test that an empty stack passes validation."""
        check_for_duplicate_commit_ids([])

    def test_single_commit_passes(self) -> None:
        """Test that a single commit passes validation."""
        commits = [
            Commit.from_strings("abc12345", "aaa" * 13 + "a", "Only commit"),
        ]
        check_for_duplicate_commit_ids(commits)

    def test_duplicate_commit_id_raises(self) -> None:
        """Test that duplicate commit-ids raise DuplicateCommitIDError."""
        commits = [
            Commit.from_strings("abc12345", "aaa" * 13 + "a", "First commit"),
            Commit.from_strings("abc12345", "bbb" * 13 + "b", "Second commit (duplicate ID)"),
            Commit.from_strings("def67890", "ccc" * 13 + "c", "Third commit"),
        ]
        with pytest.raises(DuplicateCommitIDError) as exc_info:
            check_for_duplicate_commit_ids(commits)

        # Verify the exception contains useful information
        assert "abc12345" in str(exc_info.value)
        assert "aaa" in str(exc_info.value)  # First commit hash prefix
        assert "bbb" in str(exc_info.value)  # Second commit hash prefix

    def test_multiple_duplicates_raises(self) -> None:
        """Test that multiple duplicate commit-ids are all reported."""
        commits = [
            Commit.from_strings("abc12345", "aaa" * 13 + "a", "First commit"),
            Commit.from_strings("abc12345", "bbb" * 13 + "b", "Dup of first"),
            Commit.from_strings("def67890", "ccc" * 13 + "c", "Second commit"),
            Commit.from_strings("def67890", "ddd" * 13 + "d", "Dup of second"),
        ]
        with pytest.raises(DuplicateCommitIDError) as exc_info:
            check_for_duplicate_commit_ids(commits)

        # Both duplicate IDs should be mentioned
        assert "abc12345" in str(exc_info.value)
        assert "def67890" in str(exc_info.value)

    def test_three_commits_same_id_raises(self) -> None:
        """Test that three commits with same ID are all reported."""
        commits = [
            Commit.from_strings("abc12345", "aaa" * 13 + "a", "First commit"),
            Commit.from_strings("abc12345", "bbb" * 13 + "b", "Second commit"),
            Commit.from_strings("abc12345", "ccc" * 13 + "c", "Third commit"),
        ]
        with pytest.raises(DuplicateCommitIDError) as exc_info:
            check_for_duplicate_commit_ids(commits)

        # All three commit hashes should be mentioned
        error_msg = str(exc_info.value)
        assert "abc12345" in error_msg
        assert "aaa" in error_msg
        assert "bbb" in error_msg
        assert "ccc" in error_msg

    def test_commits_without_ids_are_ignored(self) -> None:
        """Test that commits without commit-ids are ignored in duplicate check."""
        commits = [
            Commit.from_strings("", "aaa" * 13 + "a", "No ID commit"),
            Commit.from_strings("", "bbb" * 13 + "b", "Another no ID commit"),
            Commit.from_strings("abc12345", "ccc" * 13 + "c", "Has ID"),
        ]
        # Should not raise - empty IDs are not considered duplicates
        check_for_duplicate_commit_ids(commits)

    def test_exception_message_is_helpful(self) -> None:
        """Test that the exception message provides actionable guidance."""
        commits = [
            Commit.from_strings("abc12345", "aaa" * 13 + "a", "First commit"),
            Commit.from_strings("abc12345", "bbb" * 13 + "b", "Cherry-picked commit"),
        ]
        with pytest.raises(DuplicateCommitIDError) as exc_info:
            check_for_duplicate_commit_ids(commits)

        error_msg = str(exc_info.value)
        # Should mention cherry-pick as a common cause
        assert "cherry-pick" in error_msg.lower()
        # Should explain that each commit needs unique ID
        assert "unique" in error_msg.lower()


class TestParseLocalCommitStack:
    """Tests for parse_local_commit_stack function."""

    def test_parse_commits_with_duplicate_ids(self) -> None:
        """Test that parsing works even with duplicate IDs (detection happens later)."""
        # This log has two commits with the same commit-id
        commit_log = """commit aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
Author: Test User <test@example.com>
Date:   Mon Jan 1 00:00:00 2024 +0000

    First commit

    commit-id:abc12345

commit bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
Author: Test User <test@example.com>
Date:   Mon Jan 1 00:00:01 2024 +0000

    Second commit (duplicate ID)

    commit-id:abc12345
"""
        commits, valid = parse_local_commit_stack(commit_log)

        # Parsing should succeed - duplicate detection is separate
        assert valid is True
        assert len(commits) == 2

        # Both commits should have the same commit-id
        assert commits[0].commit_id == "abc12345"
        assert commits[1].commit_id == "abc12345"
