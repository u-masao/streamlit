# Copyright 2018-2022 Streamlit Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import unittest
from unittest.mock import MagicMock, mock_open, patch

import pytest
import tornado.testing

import streamlit.app_session as app_session
from streamlit import config
from streamlit.proto.ForwardMsg_pb2 import ForwardMsg
from streamlit.app_session import AppSession, AppSessionState
from streamlit.script_run_context import (
    ScriptRunContext,
    add_script_run_ctx,
    get_script_run_ctx,
)
from streamlit.script_runner import ScriptRunnerEvent
from streamlit.session_data import SessionData
from streamlit.state.session_state import SessionState
from streamlit.uploaded_file_manager import UploadedFileManager


@pytest.fixture
def del_path(monkeypatch):
    monkeypatch.setenv("PATH", "")


class AppSessionTest(unittest.TestCase):
    @patch("streamlit.app_session.secrets._file_change_listener.disconnect")
    @patch("streamlit.app_session.LocalSourcesWatcher")
    def test_shutdown(self, _, patched_disconnect):
        """Test that AppSession.shutdown behaves sanely."""
        file_mgr = MagicMock(spec=UploadedFileManager)
        rs = AppSession(None, SessionData("", ""), file_mgr, None, MagicMock())

        rs.shutdown()
        self.assertEqual(AppSessionState.SHUTDOWN_REQUESTED, rs._state)
        file_mgr.remove_session_files.assert_called_once_with(rs.id)
        patched_disconnect.assert_called_once_with(rs._on_secrets_file_changed)

        # A 2nd shutdown call should have no effect.
        rs.shutdown()
        self.assertEqual(AppSessionState.SHUTDOWN_REQUESTED, rs._state)
        file_mgr.remove_session_files.assert_called_once_with(rs.id)

    @patch("streamlit.app_session.LocalSourcesWatcher")
    def test_unique_id(self, _1):
        """Each AppSession should have a unique ID"""
        file_mgr = MagicMock(spec=UploadedFileManager)
        lsw = MagicMock()
        rs1 = AppSession(None, SessionData("", ""), file_mgr, None, lsw)
        rs2 = AppSession(None, SessionData("", ""), file_mgr, None, lsw)
        self.assertNotEqual(rs1.id, rs2.id)

    @patch("streamlit.app_session.LocalSourcesWatcher")
    def test_creates_session_state_on_init(self, _):
        rs = AppSession(
            None, SessionData("", ""), UploadedFileManager(), None, MagicMock()
        )
        self.assertTrue(isinstance(rs.session_state, SessionState))

    @patch("streamlit.app_session.LocalSourcesWatcher")
    def test_clear_cache_resets_session_state(self, _1):
        rs = AppSession(
            None, SessionData("", ""), UploadedFileManager(), None, MagicMock()
        )
        rs._session_state["foo"] = "bar"
        rs.handle_clear_cache_request()
        self.assertTrue("foo" not in rs._session_state)

    @patch("streamlit.legacy_caching.clear_cache")
    @patch("streamlit.caching.memo.clear")
    @patch("streamlit.caching.singleton.clear")
    def test_clear_cache_all_caches(
        self, clear_singleton_cache, clear_memo_cache, clear_legacy_cache
    ):
        rs = AppSession(
            MagicMock(), SessionData("", ""), UploadedFileManager(), None, MagicMock()
        )
        rs.handle_clear_cache_request()
        clear_singleton_cache.assert_called_once()
        clear_memo_cache.assert_called_once()
        clear_legacy_cache.assert_called_once()

    @patch("streamlit.app_session.secrets._file_change_listener.connect")
    def test_request_rerun_on_secrets_file_change(self, patched_connect):
        rs = AppSession(
            None, SessionData("", ""), UploadedFileManager(), None, MagicMock()
        )
        patched_connect.assert_called_once_with(rs._on_secrets_file_changed)


def _mock_get_options_for_section(overrides=None):
    if not overrides:
        overrides = {}

    theme_opts = {
        "base": "dark",
        "primaryColor": "coral",
        "backgroundColor": "white",
        "secondaryBackgroundColor": "blue",
        "textColor": "black",
        "font": "serif",
    }

    for k, v in overrides.items():
        theme_opts[k] = v

    def get_options_for_section(section):
        if section == "theme":
            return theme_opts
        return config.get_options_for_section(section)

    return get_options_for_section


class AppSessionNewSessionDataTest(tornado.testing.AsyncTestCase):
    @patch("streamlit.app_session.config")
    @patch("streamlit.app_session.LocalSourcesWatcher")
    @patch("streamlit.util.os.makedirs")
    @patch("streamlit.metrics_util.os.path.exists", MagicMock(return_value=False))
    @patch(
        "streamlit.app_session._generate_scriptrun_id",
        MagicMock(return_value="mock_scriptrun_id"),
    )
    @patch("streamlit.file_util.open", mock_open(read_data=""))
    @tornado.testing.gen_test
    def test_enqueue_new_session_message(self, _1, _2, patched_config):
        def get_option(name):
            if name == "server.runOnSave":
                # Just to avoid starting the watcher for no reason.
                return False

            return config.get_option(name)

        patched_config.get_option.side_effect = get_option
        patched_config.get_options_for_section.side_effect = (
            _mock_get_options_for_section()
        )

        # Create a AppSession with some mocked bits
        rs = AppSession(
            self.io_loop,
            SessionData("mock_report.py", ""),
            UploadedFileManager(),
            lambda: None,
            MagicMock(),
        )

        orig_ctx = get_script_run_ctx()
        ctx = ScriptRunContext(
            "TestSessionID", rs._session_data.enqueue, "", None, None
        )
        add_script_run_ctx(ctx=ctx)

        rs._on_scriptrunner_event(ScriptRunnerEvent.SCRIPT_STARTED)

        sent_messages = rs._session_data._browser_queue._queue
        self.assertEqual(len(sent_messages), 2)  # NewApp and SessionState messages

        # Note that we're purposefully not very thoroughly testing new_session
        # fields below to avoid getting to the point where we're just
        # duplicating code in tests.
        new_session_msg = sent_messages[0].new_session
        self.assertEqual("mock_scriptrun_id", new_session_msg.script_run_id)

        self.assertEqual(new_session_msg.HasField("config"), True)
        self.assertEqual(
            new_session_msg.config.allow_run_on_save,
            config.get_option("server.allowRunOnSave"),
        )

        self.assertEqual(new_session_msg.HasField("custom_theme"), True)
        self.assertEqual(new_session_msg.custom_theme.text_color, "black")

        init_msg = new_session_msg.initialize
        self.assertEqual(init_msg.HasField("user_info"), True)

        add_script_run_ctx(ctx=orig_ctx)


class PopulateCustomThemeMsgTest(unittest.TestCase):
    @patch("streamlit.app_session.config")
    def test_no_custom_theme_prop_if_no_theme(self, patched_config):
        patched_config.get_options_for_section.side_effect = (
            _mock_get_options_for_section(
                {
                    "base": None,
                    "primaryColor": None,
                    "backgroundColor": None,
                    "secondaryBackgroundColor": None,
                    "textColor": None,
                    "font": None,
                }
            )
        )

        msg = ForwardMsg()
        new_session_msg = msg.new_session
        app_session._populate_theme_msg(new_session_msg.custom_theme)

        self.assertEqual(new_session_msg.HasField("custom_theme"), False)

    @patch("streamlit.app_session.config")
    def test_can_specify_some_options(self, patched_config):
        patched_config.get_options_for_section.side_effect = _mock_get_options_for_section(
            {
                # Leave base, primaryColor, and font defined.
                "backgroundColor": None,
                "secondaryBackgroundColor": None,
                "textColor": None,
            }
        )

        msg = ForwardMsg()
        new_session_msg = msg.new_session
        app_session._populate_theme_msg(new_session_msg.custom_theme)

        self.assertEqual(new_session_msg.HasField("custom_theme"), True)
        self.assertEqual(new_session_msg.custom_theme.primary_color, "coral")
        # In proto3, primitive fields are technically always required and are
        # set to the type's zero value when undefined.
        self.assertEqual(new_session_msg.custom_theme.background_color, "")

    @patch("streamlit.app_session.config")
    def test_can_specify_all_options(self, patched_config):
        patched_config.get_options_for_section.side_effect = (
            # Specifies all options by default.
            _mock_get_options_for_section()
        )

        msg = ForwardMsg()
        new_session_msg = msg.new_session
        app_session._populate_theme_msg(new_session_msg.custom_theme)

        self.assertEqual(new_session_msg.HasField("custom_theme"), True)
        self.assertEqual(new_session_msg.custom_theme.primary_color, "coral")
        self.assertEqual(new_session_msg.custom_theme.background_color, "white")

    @patch("streamlit.app_session.LOGGER")
    @patch("streamlit.app_session.config")
    def test_logs_warning_if_base_invalid(self, patched_config, patched_logger):
        patched_config.get_options_for_section.side_effect = (
            _mock_get_options_for_section({"base": "blah"})
        )

        msg = ForwardMsg()
        new_session_msg = msg.new_session
        app_session._populate_theme_msg(new_session_msg.custom_theme)

        patched_logger.warning.assert_called_once_with(
            '"blah" is an invalid value for theme.base.'
            " Allowed values include ['light', 'dark']. Setting theme.base to \"light\"."
        )

    @patch("streamlit.app_session.LOGGER")
    @patch("streamlit.app_session.config")
    def test_logs_warning_if_font_invalid(self, patched_config, patched_logger):
        patched_config.get_options_for_section.side_effect = (
            _mock_get_options_for_section({"font": "comic sans"})
        )

        msg = ForwardMsg()
        new_session_msg = msg.new_session
        app_session._populate_theme_msg(new_session_msg.custom_theme)

        patched_logger.warning.assert_called_once_with(
            '"comic sans" is an invalid value for theme.font.'
            " Allowed values include ['sans serif', 'serif', 'monospace']. Setting theme.font to \"sans serif\"."
        )

    @patch("streamlit.app_session.LocalSourcesWatcher")
    def test_passes_client_state_on_run_on_save(self, _):
        rs = AppSession(
            None, SessionData("", ""), UploadedFileManager(), None, MagicMock()
        )
        rs._run_on_save = True
        rs.request_rerun = MagicMock()
        rs._on_source_file_changed()

        rs.request_rerun.assert_called_once_with(rs._client_state)
