#!/usr/bin/env python3

import sys
import os
import time
import tempfile
import shutil
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent))

sys.modules['requests'] = MagicMock()

import kv_cache_saver


class TestBaseNameFunctions:
    def test_set_and_get_base_name(self):
        kv_cache_saver.set_base_name("test_session")
        assert kv_cache_saver.get_base_name() == "test_session"
        assert kv_cache_saver.get_cache_pattern() == "test_session_*.bin"

    def test_cache_pattern_formatting(self):
        kv_cache_saver.set_base_name("my_session")
        pattern = kv_cache_saver.get_cache_pattern()
        assert pattern == "my_session_*.bin"


class TestGetAvailableBaseNames:
    def setup_method(self):
        self.temp_dir = tempfile.mkdtemp()
        self.original_save_dir = kv_cache_saver.SAVE_DIR
        kv_cache_saver.SAVE_DIR = Path(self.temp_dir)

    def teardown_method(self):
        shutil.rmtree(self.temp_dir)
        kv_cache_saver.SAVE_DIR = self.original_save_dir

    def test_get_available_base_names_empty(self):
        names = kv_cache_saver.get_available_base_names()
        assert names == []

    def test_get_available_base_names_single(self):
        cache_file = Path(self.temp_dir) / "session1_20250101120000.bin"
        cache_file.touch()
        names = kv_cache_saver.get_available_base_names()
        assert "session1" in names

    def test_get_available_base_names_multiple(self):
        (Path(self.temp_dir) / "session1_20250101120000.bin").touch()
        (Path(self.temp_dir) / "session1_20250101130000.bin").touch()
        (Path(self.temp_dir) / "session2_20250101120000.bin").touch()
        names = kv_cache_saver.get_available_base_names()
        assert "session1" in names
        assert "session2" in names
        assert len(names) == 2


class TestGetLatestCacheFile:
    def setup_method(self):
        self.temp_dir = tempfile.mkdtemp()
        self.original_save_dir = kv_cache_saver.SAVE_DIR
        kv_cache_saver.SAVE_DIR = Path(self.temp_dir)
        kv_cache_saver.set_base_name("test_session")

    def teardown_method(self):
        shutil.rmtree(self.temp_dir)
        kv_cache_saver.SAVE_DIR = self.original_save_dir

    def test_get_latest_cache_file_empty(self):
        result = kv_cache_saver.get_latest_cache_file()
        assert result is None

    def test_get_latest_cache_file_single(self):
        cache_file = Path(self.temp_dir) / "test_session_20250101120000.bin"
        cache_file.touch()
        result = kv_cache_saver.get_latest_cache_file()
        assert result is not None
        assert result.name == "test_session_20250101120000.bin"

    def test_get_latest_cache_file_multiple(self):
        file1 = Path(self.temp_dir) / "test_session_20250101120000.bin"
        file2 = Path(self.temp_dir) / "test_session_20250101130000.bin"
        file1.touch()
        time.sleep(0.1)
        file2.touch()
        result = kv_cache_saver.get_latest_cache_file()
        assert result is not None
        assert result.name == "test_session_20250101130000.bin"


class TestRotateCacheFiles:
    def setup_method(self):
        self.temp_dir = tempfile.mkdtemp()
        self.original_save_dir = kv_cache_saver.SAVE_DIR
        self.original_max_files = kv_cache_saver.MAX_FILES
        kv_cache_saver.SAVE_DIR = Path(self.temp_dir)
        kv_cache_saver.MAX_FILES = 3
        kv_cache_saver.set_base_name("test_session")
        self.mock_log = Mock()

    def teardown_method(self):
        shutil.rmtree(self.temp_dir)
        kv_cache_saver.SAVE_DIR = self.original_save_dir
        kv_cache_saver.MAX_FILES = self.original_max_files

    def test_rotate_cache_files_no_rotation_needed(self):
        for i in range(2):
            (Path(self.temp_dir) / f"test_session_{i}.bin").touch()
        kv_cache_saver.rotate_cache_files(self.mock_log)
        files = list(Path(self.temp_dir).glob("test_session_*.bin"))
        assert len(files) == 2

    def test_rotate_cache_files_rotation_needed(self):
        for i in range(5):
            file = Path(self.temp_dir) / f"test_session_{i}.bin"
            file.touch()
            time.sleep(0.01)
        kv_cache_saver.rotate_cache_files(self.mock_log)
        files = list(Path(self.temp_dir).glob("test_session_*.bin"))
        assert len(files) == 3


class TestSaveCache:
    def setup_method(self):
        self.temp_dir = tempfile.mkdtemp()
        self.original_save_dir = kv_cache_saver.SAVE_DIR
        kv_cache_saver.SAVE_DIR = Path(self.temp_dir)
        kv_cache_saver.set_base_name("test_session")
        self.mock_log = Mock()

    def teardown_method(self):
        shutil.rmtree(self.temp_dir)
        kv_cache_saver.SAVE_DIR = self.original_save_dir

    @patch('kv_cache_saver.requests.post')
    def test_save_cache_success(self, mock_post):
        mock_response = Mock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        result = kv_cache_saver.save_cache(self.mock_log)
        assert result is True
        mock_post.assert_called_once()
        assert "action=save" in mock_post.call_args[0][0]

    @patch('kv_cache_saver.requests.post')
    def test_save_cache_failure(self, mock_post):
        mock_response = Mock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"
        mock_post.return_value = mock_response

        result = kv_cache_saver.save_cache(self.mock_log)
        assert result is False

    @patch('kv_cache_saver.requests.post')
    def test_save_cache_exception(self, mock_post):
        mock_post.side_effect = Exception("Network error")
        result = kv_cache_saver.save_cache(self.mock_log)
        assert result is False


class TestLoadCache:
    def setup_method(self):
        self.temp_dir = tempfile.mkdtemp()
        self.original_save_dir = kv_cache_saver.SAVE_DIR
        kv_cache_saver.SAVE_DIR = Path(self.temp_dir)
        kv_cache_saver.set_base_name("test_session")
        self.mock_log = Mock()

    def teardown_method(self):
        shutil.rmtree(self.temp_dir)
        kv_cache_saver.SAVE_DIR = self.original_save_dir

    @patch('kv_cache_saver.requests.post')
    def test_load_cache_no_file(self, mock_post):
        result = kv_cache_saver.load_cache(self.mock_log)
        assert result is False
        mock_post.assert_not_called()

    @patch('kv_cache_saver.requests.post')
    def test_load_cache_success(self, mock_post):
        cache_file = Path(self.temp_dir) / "test_session_20250101120000.bin"
        cache_file.touch()
        mock_response = Mock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        result = kv_cache_saver.load_cache(self.mock_log)
        assert result is True
        mock_post.assert_called_once()
        assert "action=restore" in mock_post.call_args[0][0]

    @patch('kv_cache_saver.requests.post')
    def test_load_cache_failure(self, mock_post):
        cache_file = Path(self.temp_dir) / "test_session_20250101120000.bin"
        cache_file.touch()
        mock_response = Mock()
        mock_response.status_code = 404
        mock_response.text = "Not Found"
        mock_post.return_value = mock_response

        result = kv_cache_saver.load_cache(self.mock_log)
        assert result is False


class TestWaitForServer:
    def setup_method(self):
        self.mock_log = Mock()

    @patch('kv_cache_saver.requests.get')
    def test_wait_for_server_success(self, mock_get):
        mock_response = Mock()
        mock_response.status_code = 200
        mock_get.return_value = mock_response

        result = kv_cache_saver.wait_for_server(self.mock_log, max_retries=1)
        assert result is True

    @patch('kv_cache_saver.requests.get')
    def test_wait_for_server_failure(self, mock_get):
        mock_get.side_effect = Exception("Connection error")
        result = kv_cache_saver.wait_for_server(self.mock_log, max_retries=1, retry_delay=0.1)
        assert result is False


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])

