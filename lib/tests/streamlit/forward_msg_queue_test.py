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

"""Unit test of ForwardMsgQueue.py."""

import copy
import unittest
from typing import Tuple

from parameterized import parameterized

from streamlit import RootContainer
from streamlit.cursor import make_delta_path
from streamlit.forward_msg_queue import ForwardMsgQueue
from streamlit.elements import legacy_data_frame
from streamlit.proto.ForwardMsg_pb2 import ForwardMsg

# For the messages below, we don't really care about their contents so much as
# their general type.

NEW_SESSION_MSG = ForwardMsg()
NEW_SESSION_MSG.new_session.config.allow_run_on_save = True

TEXT_DELTA_MSG1 = ForwardMsg()
TEXT_DELTA_MSG1.delta.new_element.text.body = "text1"
TEXT_DELTA_MSG1.metadata.delta_path[:] = make_delta_path(RootContainer.MAIN, (), 0)

TEXT_DELTA_MSG2 = ForwardMsg()
TEXT_DELTA_MSG2.delta.new_element.text.body = "text2"
TEXT_DELTA_MSG2.metadata.delta_path[:] = make_delta_path(RootContainer.MAIN, (), 0)

ADD_BLOCK_MSG = ForwardMsg()
ADD_BLOCK_MSG.metadata.delta_path[:] = make_delta_path(RootContainer.MAIN, (), 0)

DF_DELTA_MSG = ForwardMsg()
legacy_data_frame.marshall_data_frame(
    {"col1": [0, 1, 2], "col2": [10, 11, 12]}, DF_DELTA_MSG.delta.new_element.data_frame
)
DF_DELTA_MSG.metadata.delta_path[:] = make_delta_path(RootContainer.MAIN, (), 0)

ADD_ROWS_MSG = ForwardMsg()
legacy_data_frame.marshall_data_frame(
    {"col1": [3, 4, 5], "col2": [13, 14, 15]}, ADD_ROWS_MSG.delta.add_rows.data
)
ADD_ROWS_MSG.metadata.delta_path[:] = make_delta_path(RootContainer.MAIN, (), 0)


class ForwardMsgQueueTest(unittest.TestCase):
    def test_simple_enqueue(self):
        rq = ForwardMsgQueue()
        self.assertTrue(rq.is_empty())

        rq.enqueue(NEW_SESSION_MSG)

        self.assertFalse(rq.is_empty())
        queue = rq.flush()
        self.assertTrue(rq.is_empty())
        self.assertEqual(len(queue), 1)
        self.assertTrue(queue[0].new_session.config.allow_run_on_save)

    def test_enqueue_two(self):
        rq = ForwardMsgQueue()
        self.assertTrue(rq.is_empty())

        rq.enqueue(NEW_SESSION_MSG)

        TEXT_DELTA_MSG1.metadata.delta_path[:] = make_delta_path(
            RootContainer.MAIN, (), 0
        )
        rq.enqueue(TEXT_DELTA_MSG1)

        queue = rq.flush()
        self.assertEqual(len(queue), 2)
        self.assertEqual(
            make_delta_path(RootContainer.MAIN, (), 0), queue[1].metadata.delta_path
        )
        self.assertEqual(queue[1].delta.new_element.text.body, "text1")

    def test_enqueue_three(self):
        rq = ForwardMsgQueue()
        self.assertTrue(rq.is_empty())

        rq.enqueue(NEW_SESSION_MSG)

        TEXT_DELTA_MSG1.metadata.delta_path[:] = make_delta_path(
            RootContainer.MAIN, (), 0
        )
        rq.enqueue(TEXT_DELTA_MSG1)

        TEXT_DELTA_MSG2.metadata.delta_path[:] = make_delta_path(
            RootContainer.MAIN, (), 1
        )
        rq.enqueue(TEXT_DELTA_MSG2)

        queue = rq.flush()
        self.assertEqual(len(queue), 3)
        self.assertEqual(
            make_delta_path(RootContainer.MAIN, (), 0), queue[1].metadata.delta_path
        )
        self.assertEqual(queue[1].delta.new_element.text.body, "text1")
        self.assertEqual(
            make_delta_path(RootContainer.MAIN, (), 1), queue[2].metadata.delta_path
        )
        self.assertEqual(queue[2].delta.new_element.text.body, "text2")

    def test_replace_element(self):
        rq = ForwardMsgQueue()
        self.assertTrue(rq.is_empty())

        rq.enqueue(NEW_SESSION_MSG)

        TEXT_DELTA_MSG1.metadata.delta_path[:] = make_delta_path(
            RootContainer.MAIN, (), 0
        )
        rq.enqueue(TEXT_DELTA_MSG1)

        TEXT_DELTA_MSG2.metadata.delta_path[:] = make_delta_path(
            RootContainer.MAIN, (), 0
        )
        rq.enqueue(TEXT_DELTA_MSG2)

        queue = rq.flush()
        self.assertEqual(len(queue), 2)
        self.assertEqual(
            make_delta_path(RootContainer.MAIN, (), 0), queue[1].metadata.delta_path
        )
        self.assertEqual(queue[1].delta.new_element.text.body, "text2")

    @parameterized.expand([(TEXT_DELTA_MSG1,), (ADD_BLOCK_MSG,)])
    def test_dont_replace_block(self, other_msg: ForwardMsg):
        """add_block deltas should never be replaced/composed because they can
        have dependent deltas later in the queue."""
        rq = ForwardMsgQueue()
        self.assertTrue(rq.is_empty())

        ADD_BLOCK_MSG.metadata.delta_path[:] = make_delta_path(
            RootContainer.MAIN, (), 0
        )

        other_msg.metadata.delta_path[:] = make_delta_path(RootContainer.MAIN, (), 0)

        # Delta messages should not replace `add_block` deltas with the
        # same delta_path.
        rq.enqueue(ADD_BLOCK_MSG)
        rq.enqueue(other_msg)
        queue = rq.flush()
        self.assertEqual(len(queue), 2)
        self.assertEqual(queue[0], ADD_BLOCK_MSG)
        self.assertEqual(queue[1], other_msg)

    def test_simple_add_rows(self):
        rq = ForwardMsgQueue()
        self.assertTrue(rq.is_empty())

        rq.enqueue(NEW_SESSION_MSG)

        TEXT_DELTA_MSG1.metadata.delta_path[:] = make_delta_path(
            RootContainer.MAIN, (), 0
        )
        rq.enqueue(TEXT_DELTA_MSG1)

        DF_DELTA_MSG.metadata.delta_path[:] = make_delta_path(RootContainer.MAIN, (), 1)
        rq.enqueue(DF_DELTA_MSG)

        ADD_ROWS_MSG.metadata.delta_path[:] = make_delta_path(RootContainer.MAIN, (), 1)
        rq.enqueue(ADD_ROWS_MSG)

        queue = rq.flush()
        self.assertEqual(len(queue), 3)
        self.assertEqual(
            make_delta_path(RootContainer.MAIN, (), 0), queue[1].metadata.delta_path
        )
        self.assertEqual(queue[1].delta.new_element.text.body, "text1")
        self.assertEqual(
            make_delta_path(RootContainer.MAIN, (), 1), queue[2].metadata.delta_path
        )
        col0 = queue[2].delta.new_element.data_frame.data.cols[0].int64s.data
        col1 = queue[2].delta.new_element.data_frame.data.cols[1].int64s.data
        self.assertEqual(col0, [0, 1, 2, 3, 4, 5])
        self.assertEqual(col1, [10, 11, 12, 13, 14, 15])

    def test_add_rows_rerun(self):
        rq = ForwardMsgQueue()
        self.assertTrue(rq.is_empty())

        rq.enqueue(NEW_SESSION_MSG)

        # Simulate rerun
        for i in range(2):
            TEXT_DELTA_MSG1.metadata.delta_path[:] = make_delta_path(
                RootContainer.MAIN, (), 0
            )
            rq.enqueue(TEXT_DELTA_MSG1)

            DF_DELTA_MSG.metadata.delta_path[:] = make_delta_path(
                RootContainer.MAIN, (), 1
            )
            rq.enqueue(DF_DELTA_MSG)

            ADD_ROWS_MSG.metadata.delta_path[:] = make_delta_path(
                RootContainer.MAIN, (), 1
            )
            rq.enqueue(ADD_ROWS_MSG)

        queue = rq.flush()
        self.assertEqual(len(queue), 3)
        self.assertEqual(
            make_delta_path(RootContainer.MAIN, (), 0), queue[1].metadata.delta_path
        )
        self.assertEqual(queue[1].delta.new_element.text.body, "text1")
        self.assertEqual(
            make_delta_path(RootContainer.MAIN, (), 1), queue[2].metadata.delta_path
        )
        col0 = queue[2].delta.new_element.data_frame.data.cols[0].int64s.data
        col1 = queue[2].delta.new_element.data_frame.data.cols[1].int64s.data
        self.assertEqual(col0, [0, 1, 2, 3, 4, 5])
        self.assertEqual(col1, [10, 11, 12, 13, 14, 15])

    def test_multiple_containers(self):
        """Deltas should only be coalesced if they're in the same container"""
        rq = ForwardMsgQueue()
        self.assertTrue(rq.is_empty())

        rq.enqueue(NEW_SESSION_MSG)

        def enqueue_deltas(container: RootContainer, path: Tuple[int, ...]):
            # We deep-copy the protos because we mutate each one
            # multiple times.
            msg = copy.deepcopy(TEXT_DELTA_MSG1)
            msg.metadata.delta_path[:] = make_delta_path(container, path, 0)
            rq.enqueue(msg)

            msg = copy.deepcopy(DF_DELTA_MSG)
            msg.metadata.delta_path[:] = make_delta_path(container, path, 1)
            rq.enqueue(msg)

            msg = copy.deepcopy(ADD_ROWS_MSG)
            msg.metadata.delta_path[:] = make_delta_path(container, path, 1)
            rq.enqueue(msg)

        enqueue_deltas(RootContainer.MAIN, ())
        enqueue_deltas(RootContainer.SIDEBAR, (0, 0, 1))

        def assert_deltas(container: RootContainer, path: Tuple[int, ...], idx: int):
            self.assertEqual(
                make_delta_path(container, path, 0), queue[idx].metadata.delta_path
            )
            self.assertEqual("text1", queue[idx].delta.new_element.text.body)

            self.assertEqual(
                make_delta_path(container, path, 1), queue[idx + 1].metadata.delta_path
            )
            col0 = queue[idx + 1].delta.new_element.data_frame.data.cols[0].int64s.data
            col1 = queue[idx + 1].delta.new_element.data_frame.data.cols[1].int64s.data
            self.assertEqual([0, 1, 2, 3, 4, 5], col0)
            self.assertEqual([10, 11, 12, 13, 14, 15], col1)

        queue = rq.flush()
        self.assertEqual(5, len(queue))

        assert_deltas(RootContainer.MAIN, (), 1)
        assert_deltas(RootContainer.SIDEBAR, (0, 0, 1), 3)
