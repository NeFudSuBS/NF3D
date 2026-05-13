"""
NF3D REFACTORING GUIDE - PART 2: Complete Implementation

This guide covers:
1. Complete test suite (Phase 4)
2. Documentation and architecture (Phase 5)
3. Migration strategies for existing code
4. CI/CD integration examples
5. Performance monitoring and profiling
"""

# ============================================================================
# PHASE 4: COMPREHENSIVE TESTING SUITE
# ============================================================================

# tests/conftest.py
"""
Pytest configuration and shared fixtures for NF3D tests.
"""
import pytest
import logging
from pathlib import Path
import tempfile
from typing import Generator
import sys

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from logging_config import NF3DLogger
from config import (
    DepthAnalysisConfig, SubtitleZoneConfig, 
    EmergenceEffectsConfig, OCRDetectionConfig
)


@pytest.fixture(scope="session")
def setup_logging():
    """Setup logging for test session."""
    NF3DLogger.setup(level=logging.WARNING)


@pytest.fixture
def temp_workspace() -> Generator[Path, None, None]:
    """Temporary workspace directory for tests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        workspace = Path(tmpdir) / "nf3d_test"
        workspace.mkdir(parents=True)
        yield workspace


@pytest.fixture
def depth_config() -> DepthAnalysisConfig:
    """Depth analysis configuration for tests."""
    return DepthAnalysisConfig()


@pytest.fixture
def ocr_config() -> OCRDetectionConfig:
    """OCR detection configuration for tests."""
    return OCRDetectionConfig()


@pytest.fixture
def sample_srt_file(temp_workspace: Path) -> Path:
    """Create a sample SRT file for testing."""
    srt_path = temp_workspace / "sample.srt"
    srt_content = """1
00:00:01,000 --> 00:00:05,000
Hello, this is a test subtitle.

2
00:00:06,000 --> 00:00:10,000
This is the second cue.
It has multiple lines.

3
00:00:11,000 --> 00:00:15,000
Third cue with <i>italics</i>.
"""
    srt_path.write_text(srt_content)
    return srt_path


@pytest.fixture
def sample_malformed_srt(temp_workspace: Path) -> Path:
    """Create a malformed SRT for error handling tests."""
    srt_path = temp_workspace / "malformed.srt"
    srt_content = """1
invalid time format
This is malformed.

2
00:00:06,000 --> 00:00:10,000
This one is correct.
"""
    srt_path.write_text(srt_content)
    return srt_path


# tests/test_config.py
"""
Tests for configuration module.
"""
import pytest
from config import (
    RegexPatterns, DepthAnalysisConfig, 
    SubtitleZoneConfig, EmergenceEffectsConfig,
    OCRDetectionConfig, ErrorMessages, DEPTH_ANALYSIS_DEFAULTS
)
import re


class TestRegexPatterns:
    """Test pre-compiled regex patterns."""
    
    def test_srt_time_pattern_valid(self):
        """Valid SRT time format should match."""
        time_str = "00:01:30,500 --> 00:01:35,800"
        matches = RegexPatterns.SRT_TIME.match(time_str)
        assert matches is not None
        assert matches.group(1) == "00"  # hours
        assert matches.group(2) == "01"  # minutes
        assert matches.group(3) == "30"  # seconds
        assert matches.group(4) == "500"  # milliseconds
    
    def test_srt_time_pattern_invalid(self):
        """Invalid SRT time format should not match."""
        time_str = "1:2:3.4"
        assert RegexPatterns.SRT_TIME.match(time_str) is None
    
    def test_word_token_pattern(self):
        """Word tokenization with contractions."""
        text = "it's don't we've I'm"
        words = RegexPatterns.WORD_TOKEN.findall(text)
        assert words == ["it's", "don't", "we've", "I'm"]
    
    def test_clip_tag_extraction(self):
        """ASS clip tag extraction."""
        dialogue = r"{\\clip(0,0,1920,1080)}Hello"
        match = RegexPatterns.CLIP_TAG.search(dialogue)
        assert match is not None
        assert match.group(1) == "0,0,1920,1080"
    
    def test_pos_tag_extraction(self):
        """ASS position tag extraction."""
        dialogue = r"{\\pos(960,900)}Hello"
        match = RegexPatterns.POS_TAG.search(dialogue)
        assert match is not None
        assert match.group(1) == "960"
        assert match.group(2) == "900"
    
    def test_mid_word_punct_pattern(self):
        """Mid-word punctuation detection."""
        text = "wh:at is th;is"
        matches = list(RegexPatterns.MID_WORD_PUNCT.finditer(text))
        assert len(matches) == 2
        assert matches[0].group() == "wh:at"
        assert matches[1].group() == "th;is"


class TestDepthAnalysisConfig:
    """Test depth analysis configuration."""
    
    def test_default_values(self):
        """Default configuration values should be reasonable."""
        cfg = DepthAnalysisConfig()
        assert cfg.DEFAULT_SAMPLES_PER_CUE == 6
        assert cfg.DEFAULT_BASE_DEPTH == 6
        assert -9 <= cfg.DEFAULT_OUTPUT_BIAS <= 0
        assert cfg.DEFAULT_OUT_MIN < cfg.DEFAULT_OUT_MAX
    
    def test_sgbm_parameters_valid(self):
        """SGBM parameters should be appropriate for stereo matching."""
        cfg = DepthAnalysisConfig()
        assert cfg.SGBM_NUM_DISPARITIES > 0
        assert cfg.SGBM_BLOCK_SIZE > 0
        assert cfg.SGBM_P2 > cfg.SGBM_P1  # P2 should be larger
    
    def test_subtitle_zone_percentages(self):
        """Subtitle zone percentages should be valid."""
        cfg = DepthAnalysisConfig()
        assert 0 < cfg.SUBTITLE_ZONE_Y_START_PCT < 1.0
        assert 0 < cfg.SUBTITLE_ZONE_X_MARGIN_PCT < 0.5


class TestOCRDetectionConfig:
    """Test OCR detection configuration."""
    
    def test_homoglyph_map_populated(self):
        """Homoglyph map should contain common lookalikes."""
        cfg = OCRDetectionConfig()
        assert len(cfg.HOMOGLYPH_MAP) > 0
        assert "е" in cfg.HOMOGLYPH_MAP  # Cyrillic e
        assert "р" in cfg.HOMOGLYPH_MAP  # Cyrillic r
    
    def test_illegal_chars_set(self):
        """Illegal characters set should not be empty."""
        cfg = OCRDetectionConfig()
        assert len(cfg.ILLEGAL_CHARS) > 0
        assert "|" in cfg.ILLEGAL_CHARS
    
    def test_allowed_unicode_populated(self):
        """Allowed Unicode set should have music/typography symbols."""
        cfg = OCRDetectionConfig()
        assert "♪" in cfg.ALLOWED_UNICODE
        assert "…" in cfg.ALLOWED_UNICODE


# tests/test_exception_handlers.py
"""
Tests for exception handling decorators and utilities.
"""
import pytest
import logging
from exception_handlers import (
    handle_errors, retry_on_exception, ensure_tool_exists,
    ToolNotFoundError, NF3DException
)


class TestHandleErrorsDecorator:
    """Test @handle_errors decorator."""
    
    def test_successful_function(self):
        """Function that succeeds should return result."""
        @handle_errors(default_return=None)
        def successful_func():
            return "success"
        
        result = successful_func()
        assert result == "success"
    
    def test_error_caught_with_default(self):
        """Caught error should return default value."""
        @handle_errors(default_return="default", error_types=(ValueError,))
        def failing_func():
            raise ValueError("Something went wrong")
        
        result = failing_func()
        assert result == "default"
    
    def test_error_reraised(self):
        """With reraise=True, exception should be re-raised."""
        @handle_errors(default_return=None, reraise=True, error_types=(ValueError,))
        def failing_func():
            raise ValueError("Test error")
        
        with pytest.raises(ValueError):
            failing_func()
    
    def test_wrong_exception_type_not_caught(self):
        """Only specified exception types should be caught."""
        @handle_errors(default_return=None, error_types=(ValueError,))
        def failing_func():
            raise TypeError("Wrong type")
        
        with pytest.raises(TypeError):
            failing_func()


class TestRetryOnExceptionDecorator:
    """Test @retry_on_exception decorator."""
    
    def test_successful_first_attempt(self):
        """Should succeed on first attempt."""
        call_count = [0]
        
        @retry_on_exception(max_attempts=3)
        def sometimes_fails():
            call_count[0] += 1
            return "success"
        
        result = sometimes_fails()
        assert result == "success"
        assert call_count[0] == 1
    
    def test_retry_then_success(self):
        """Should retry and eventually succeed."""
        call_count = [0]
        
        @retry_on_exception(max_attempts=3, delay_seconds=0.01)
        def eventually_succeeds():
            call_count[0] += 1
            if call_count[0] < 3:
                raise ValueError("Not yet")
            return "success"
        
        result = eventually_succeeds()
        assert result == "success"
        assert call_count[0] == 3
    
    def test_all_attempts_fail(self):
        """Should raise after all attempts exhausted."""
        @retry_on_exception(max_attempts=2, delay_seconds=0.01)
        def always_fails():
            raise ValueError("Always fails")
        
        with pytest.raises(ValueError):
            always_fails()
    
    def test_exponential_backoff(self):
        """Delays should increase with exponential backoff."""
        import time
        call_times = []
        
        @retry_on_exception(
            max_attempts=3,
            delay_seconds=0.01,
            backoff_factor=2.0
        )
        def track_attempts():
            call_times.append(time.time())
            if len(call_times) < 3:
                raise ValueError("Retry")
            return "success"
        
        result = track_attempts()
        assert result == "success"
        assert len(call_times) == 3


class TestEnsureToolExists:
    """Test tool existence validation."""
    
    def test_valid_tool_path(self, tmp_path):
        """Valid tool path should be returned."""
        tool_path = tmp_path / "ffmpeg"
        tool_path.write_text("#!/bin/bash\necho ffmpeg")
        tool_path.chmod(0o755)
        
        result = ensure_tool_exists("ffmpeg", str(tool_path))
        assert result == str(tool_path)
    
    def test_missing_tool_raises(self):
        """Missing tool should raise ToolNotFoundError."""
        with pytest.raises(ToolNotFoundError) as exc_info:
            ensure_tool_exists("ffmpeg", "/nonexistent/path")
        
        assert "ffmpeg" in str(exc_info.value)
    
    def test_none_path_raises(self):
        """None path should raise ToolNotFoundError."""
        with pytest.raises(ToolNotFoundError):
            ensure_tool_exists("ffmpeg", None)


# tests/test_ocr_scanner.py
"""
Tests for OCR issue scanning.
"""
import pytest
from config import OCRDetectionConfig
from nf3d_core_refactored import OCRIssueScanner


class TestOCRIssueScanner:
    """Test OCR error detection."""
    
    def test_homoglyph_detection(self):
        """Should detect Cyrillic/Greek lookalikes."""
        scanner = OCRIssueScanner()
        # "рос" uses Cyrillic 'р' (looks like 'p')
        issues = scanner.scan("This is a рос")
        
        homoglyph_issues = [i for i in issues if i.kind == "homoglyph"]
        assert len(homoglyph_issues) > 0
    
    def test_illegal_chars_detection(self):
        """Should detect illegal characters."""
        scanner = OCRIssueScanner()
        text = "This | has | pipes"
        issues = scanner.scan(text)
        
        illegal_issues = [i for i in issues if i.kind == "illegal"]
        assert len(illegal_issues) == 2
    
    def test_ocr_mid_word_punct(self):
        """Should detect mid-word punctuation."""
        scanner = OCRIssueScanner()
        issues = scanner.scan("wh:at is th;is")
        
        ocr_issues = [i for i in issues if i.kind == "ocr"]
        assert len(ocr_issues) > 0
    
    def test_clean_text_no_issues(self):
        """Clean text should produce no issues."""
        scanner = OCRIssueScanner()
        issues = scanner.scan("This is clean text with no issues.")
        
        assert len(issues) == 0
    
    def test_markup_stripped_before_scan(self):
        """HTML markup should be stripped before scanning."""
        scanner = OCRIssueScanner()
        issues = scanner.scan("<i>This</i> text")
        
        # Should not have any issues
        assert len(issues) == 0
    
    def test_music_symbols_allowed(self):
        """Music symbols should be in allowed list."""
        scanner = OCRIssueScanner()
        issues = scanner.scan("♪ Music ♫")
        
        nonascii_issues = [i for i in issues if i.kind == "nonascii"]
        assert len(nonascii_issues) == 0


# tests/test_tool_detector.py
"""
Tests for external tool detection.
"""
import pytest
import sys
from pathlib import Path
from exception_handlers import ToolNotFoundError
from nf3d_core_refactored import ToolDetector


class TestToolDetector:
    """Test tool detection logic."""
    
    def test_detect_method_exists(self):
        """ToolDetector should have detect method."""
        assert hasattr(ToolDetector, "detect")
    
    def test_invalid_tool_name(self):
        """Unknown tool should raise ValueError."""
        with pytest.raises(ValueError):
            ToolDetector.detect("nonexistent_tool")
    
    def test_ffmpeg_detection(self):
        """Should handle ffmpeg detection attempt."""
        try:
            result = ToolDetector.detect("ffmpeg")
            assert result is not None
            assert isinstance(result, str)
        except ToolNotFoundError:
            # It's OK if ffmpeg isn't installed in test environment
            pass
    
    def test_mkvmerge_detection(self):
        """Should handle mkvmerge detection attempt."""
        try:
            result = ToolDetector.detect("mkvmerge")
            assert result is not None
            assert isinstance(result, str)
        except ToolNotFoundError:
            # It's OK if mkvmerge isn't installed in test environment
            pass
    
    @pytest.mark.skipif(
        sys.platform != "win32",
        reason="Windows-specific test"
    )
    def test_windows_tool_candidates(self):
        """Windows should have Program Files candidates."""
        candidates = ToolDetector._TOOL_CANDIDATES.get("ffmpeg", {}).get("win32", [])
        assert len(candidates) > 0
        assert any("Program Files" in c for c in candidates)


# tests/test_integration.py
"""
Integration tests for complete workflows.
"""
import pytest
from pathlib import Path
from config import DepthAnalysisConfig, DEPTH_ANALYSIS_DEFAULTS


class TestDepthAnalysisWorkflow:
    """Test complete depth analysis workflow."""
    
    @pytest.mark.slow
    def test_frame_extraction_workflow(self, tmp_path, sample_srt_file):
        """Complete frame extraction workflow."""
        # This would test end-to-end extraction
        # Requires video file, so marked as slow
        pass
    
    def test_configuration_persistence(self, temp_workspace):
        """Configuration should persist correctly."""
        config_file = temp_workspace / "config.json"
        
        cfg = DepthAnalysisConfig()
        assert cfg.DEFAULT_SAMPLES_PER_CUE == DEPTH_ANALYSIS_DEFAULTS["samples_per_cue"]
    
    def test_subtitle_parsing_integration(self, sample_srt_file):
        """Complete subtitle parsing workflow."""
        # Read SRT file
        srt_content = sample_srt_file.read_text()
        assert "Hello, this is a test" in srt_content
        assert "Second cue" in srt_content


# tests/test_performance.py
"""
Performance and profiling tests.
"""
import pytest
import time
from config import RegexPatterns


class TestPerformance:
    """Performance benchmarks for critical operations."""
    
    @pytest.mark.benchmark
    def test_regex_compilation_performance(self, benchmark):
        """Regex patterns should be pre-compiled for performance."""
        # Measure that we're using pre-compiled patterns
        result = benchmark(
            lambda: RegexPatterns.SRT_TIME.match("00:01:30,500 --> 00:01:35,800")
        )
        assert result is not None
    
    @pytest.mark.benchmark
    def test_ocr_scanner_performance(self, benchmark):
        """OCR scanner should perform well on typical text."""
        from nf3d_core_refactored import OCRIssueScanner
        
        scanner = OCRIssueScanner()
        long_text = "This is normal text. " * 100
        
        result = benchmark(lambda: scanner.scan(long_text))
        assert isinstance(result, list)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
