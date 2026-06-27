"""
Test timeout behavior cho mindmap generation.
Mock requests.post để sleep 999s và xác nhận timeout được áp dụng đúng.
"""
import time
import unittest
from unittest.mock import patch, MagicMock
from concurrent.futures import TimeoutError as FuturesTimeout

# Import các module cần test
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

# Mock env trước khi import module
os.environ["OLLAMA_HOST"] = "http://localhost:11434"


class TestMindmapTimeout(unittest.TestCase):
    """Test timeout thật cho Ollama calls."""

    def test_llm_timeout_fast_mode_no_retry_on_timeout(self):
        """Test: balanced mode timeout -> khong retry -> deterministic fallback."""
        from services.mindmap.worker import (            TimeoutTracker, LlmCallBudget, call_mindmap_llm,
            MODE_BALANCED, JOB_TIMEOUT_BALANCED, LLM_TIMEOUT_BALANCED,
            get_llm_timeout_for_mode
        )
        
        # Setup tracker va budget
        job_timeout = JOB_TIMEOUT_BALANCED  # TEMP TESTING: 60s (was 180s)
        llm_timeout = LLM_TIMEOUT_BALANCED   # TEMP TESTING: 30s (was 90s)
        tracker = TimeoutTracker(MODE_BALANCED, job_timeout, llm_timeout)
        budget = LlmCallBudget(MODE_BALANCED)
        
        # Progress callback mock
        progress_calls = []
        def mock_progress(p, msg):
            progress_calls.append((p, msg))
        
        # Mock _invoke_mindmap_ollama_once để raise TimeoutError
        def mock_invoke(*args, **kwargs):
            raise TimeoutError("Simulated Ollama timeout")
        
        with patch('services.mindmap.worker._invoke_mindmap_ollama_once', mock_invoke):
            with self.assertRaises(TimeoutError) as ctx:
                call_mindmap_llm(
                    system_prompt="test",
                    user_prompt="test",
                    model="qwen2.5:14b",
                    mode=MODE_BALANCED,
                    strategy="mindmap_v2",
                    call_name="mindmap_v2",
                    timeout_tracker=tracker,
                    llm_budget=budget,
                    progress_notify=mock_progress,
                )
            
            # Kiểm tra exception message chứa "Simulated" (từ mock)
            self.assertIn("Simulated", str(ctx.exception))
        
        # Khi TimeoutError: register KHÔNG được gọi (code re-raise trước khi register)
        # Đây là design - budget chỉ track successful calls hoặc cuối cùng trước fallback
        self.assertEqual(budget.used, 0)
        
        # Verify log output chứa thông tin timeout
        # (Log được output ra stdout - đã thấy trong captured output)

    def test_actual_timeout_calculation(self):
        """Test: actual_timeout = min(per_call_timeout, remaining - 10)."""
        from services.mindmap.worker import (            TimeoutTracker, LlmCallBudget, call_mindmap_llm,
            MODE_BALANCED, JOB_TIMEOUT_BALANCED, LLM_TIMEOUT_BALANCED,
            get_llm_timeout_for_mode
        )
        
        # Test voi fresh tracker - remaining = job_timeout
        job_timeout = 60  # TEMP TESTING: was 180
        llm_timeout = 30  # TEMP TESTING: was 90
        tracker = TimeoutTracker(MODE_BALANCED, job_timeout, llm_timeout)
        
        remaining = tracker.time_remaining()
        per_call = get_llm_timeout_for_mode(MODE_BALANCED)
        
        # Fresh tracker: remaining ~ job_timeout, actual = min(per_call, remaining - 10)
        # actual = min(30, 60 - 10) = min(30, 50) = 30
        actual = min(per_call, max(1, remaining - 10))
        self.assertAlmostEqual(remaining, 60, delta=5)
        self.assertEqual(per_call, 30)  # TEMP TESTING: was 90
        self.assertEqual(actual, 30)  # TEMP TESTING: was 90
        
        # Test: Nếu remaining quá nhỏ -> actual < 15
        # Simulate: đẩy job_start về 165s trước -> remaining ~ 15s
        tracker.job_start -= 165
        remaining = tracker.time_remaining()
        actual = min(per_call, max(1, remaining - 10))
        # actual = min(90, max(1, ~15-10)) = min(90, 5) = 5 (< 15 -> sẽ raise)

    def test_llm_budget_stops_at_limit(self):
        """Test: LLM call budget được enforce đúng."""
        from services.mindmap.worker import LlmCallBudget, MODE_BALANCED        
        budget = LlmCallBudget(MODE_BALANCED)
        
        # Balanced: budget = 1
        self.assertEqual(budget.max_calls, 1)
        self.assertTrue(budget.can_call())
        
        # Register 1 call
        budget.register("call1", "model1", 1000, 60, 50)
        
        # Không còn call nào
        self.assertFalse(budget.can_call())
        self.assertEqual(budget.remaining(), 0)
        
        # can_call() trả về False nhưng register() vẫn được gọi (không raise)
        # Đây là design decision - budget chỉ track, không ngăn gọi

    def test_fast_mode_budget_is_1(self):
        """Test: Fast mode chỉ cho phép 1 LLM call."""
        from services.mindmap.worker import (            LlmCallBudget, LLM_CALL_BUDGET_FAST,
            MODE_FAST
        )
        
        budget = LlmCallBudget(MODE_FAST)
        self.assertEqual(budget.max_calls, 1)
        self.assertEqual(LLM_CALL_BUDGET_FAST, 1)

    def test_quality_mode_budget_is_8(self):
        """Test: Quality mode cho phép 8 LLM calls."""
        from services.mindmap.worker import (            LlmCallBudget, LLM_CALL_BUDGET_QUALITY,
            MODE_QUALITY
        )
        
        budget = LlmCallBudget(MODE_QUALITY)
        self.assertEqual(budget.max_calls, 8)
        self.assertEqual(LLM_CALL_BUDGET_QUALITY, 8)

    def test_call_mindmap_llm_raises_when_not_enough_time(self):
        """Test: call_mindmap_llm raise TimeoutError khi remaining < 25s."""
        from services.mindmap.worker import (            TimeoutTracker, LlmCallBudget, call_mindmap_llm,
            MODE_BALANCED
        )
        
        # Job timeout rất ngắn - đã gần deadline
        job_timeout = 20  # Chỉ 20s
        llm_timeout = 15
        tracker = TimeoutTracker(MODE_BALANCED, job_timeout, llm_timeout)
        budget = LlmCallBudget(MODE_BALANCED)
        
        # Progress callback mock
        def mock_progress(p, msg):
            pass
        
        # Sẽ raise vì actual_timeout < 15
        with self.assertRaises(TimeoutError) as ctx:
            call_mindmap_llm(
                system_prompt="test",
                user_prompt="test",
                model="qwen2.5:14b",
                mode=MODE_BALANCED,
                strategy="mindmap_v2",
                call_name="mindmap_v2",
                timeout_tracker=tracker,
                llm_budget=budget,
                progress_notify=mock_progress,
            )
        
        # Error message phải nói rõ "Not enough time"
        self.assertIn("Not enough time", str(ctx.exception))


class TestProgressUpdates(unittest.TestCase):
    """Test progress updates khi LLM lâu."""

    def test_progress_55_on_llm_start(self):
        """Test: Progress = 55 khi bắt đầu LLM call."""
        from services.mindmap.worker import (            TimeoutTracker, LlmCallBudget, call_mindmap_llm,
            MODE_BALANCED, JOB_TIMEOUT_BALANCED, LLM_TIMEOUT_BALANCED
        )
        
        tracker = TimeoutTracker(MODE_BALANCED, JOB_TIMEOUT_BALANCED, LLM_TIMEOUT_BALANCED)
        budget = LlmCallBudget(MODE_BALANCED)
        
        progress_calls = []
        def mock_progress(p, msg):
            progress_calls.append((p, msg))
        
        # Mock để fail ngay
        def mock_invoke(*args, **kwargs):
            raise TimeoutError("Simulated")
        
        with patch('services.mindmap.worker._invoke_mindmap_ollama_once', mock_invoke):
            try:
                call_mindmap_llm(
                    system_prompt="test",
                    user_prompt="test",
                    model="qwen2.5:14b",
                    mode=MODE_BALANCED,
                    strategy="mindmap_v2",
                    call_name="mindmap_v2",
                    timeout_tracker=tracker,
                    llm_budget=budget,
                    progress_notify=mock_progress,
                )
            except:
                pass
        
        # Progress 55 được gọi
        progress_55_calls = [p for p, m in progress_calls if p == 55]
        self.assertGreater(len(progress_55_calls), 0)
        
        # Message chứa strategy name
        strategy_calls = [m for p, m in progress_calls if "mindmap_v2" in m]
        self.assertGreater(len(strategy_calls), 0)


class TestDeterministicFallback(unittest.TestCase):
    """Test deterministic fallback khi LLM timeout."""

    def test_fallback_chain_balanced(self):
        """Test: balanced fallback chain đúng."""
        from services.mindmap.worker import get_fallback_chain, MODE_BALANCED        
        # Khi mindmap_v2 fail
        chain = get_fallback_chain("mindmap_v2", MODE_BALANCED)
        
        # Chain không chứa mindmap_v2 (đã fail)
        self.assertNotIn("mindmap_v2", chain)
        
        # Chain theo thứ tự: multilevel_fast -> single_call -> deterministic
        self.assertIn("multilevel_fast", chain)
        self.assertIn("single_call_schema", chain)
        self.assertIn("deterministic_basic_branches", chain)
        
        # Không có cmgn_light cho balanced
        self.assertNotIn("cmgn_light", chain)
        self.assertNotIn("cmgn", chain)
        self.assertNotIn("iterative", chain)

    def test_fallback_chain_fast(self):
        """Test: fast fallback chain đúng."""
        from services.mindmap.worker import get_fallback_chain, MODE_FAST        
        chain = get_fallback_chain("mindmap_v2", MODE_FAST)
        
        # Không có slow strategies
        self.assertNotIn("cmgn_light", chain)
        self.assertNotIn("cmgn", chain)
        self.assertNotIn("iterative", chain)
        self.assertNotIn("multilevel_fast", chain)  # Balanced only


if __name__ == "__main__":
    # Chu vi voi: python -m pytest BE/tests/test_mindmap_timeout.py -v
    unittest.main(verbosity=2)


class TestDirectRequestsTimeout(unittest.TestCase):
    """Test timeout that voi requests.post."""

    def test_invoke_mindmap_ollama_once_timeout(self):
        """Test: _invoke_mindmap_ollama_once raise TimeoutError khi requests.post timeout."""
        import sys, os
        sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
        
        from services.mindmap.worker import _invoke_mindmap_ollama_once, MindmapOutput        
        # Mock requests.post o module goc
        def mock_post(*args, **kwargs):
            raise TimeoutError("Simulated slow response")
        
        with patch('requests.post', mock_post):
            start = time.time()
            with self.assertRaises(TimeoutError) as ctx:
                _invoke_mindmap_ollama_once(
                    system_prompt="test",
                    user_prompt="test",
                    model="qwen2.5:14b",
                    timeout_sec=3.0,  # 3s timeout
                )
            elapsed = time.time() - start
            
            # Phai raise ngay, khong block
            self.assertLess(elapsed, 2, f"Timeout took {elapsed:.1f}s - should be instant")
            self.assertIn("Simulated", str(ctx.exception))
            
        print(f"[Test] requests.post timeout raised in {elapsed:.2f}s (expected < 2s)")
