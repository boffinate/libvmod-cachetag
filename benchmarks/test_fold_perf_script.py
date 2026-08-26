import unittest

from fold_perf_script import fold


class FoldPerfScriptTest(unittest.TestCase):
    def test_folds_samples_outermost_first_and_labels_kernel(self) -> None:
        script = [
            "cache-worker 1 2.0: 144 cpu/cycles/P:\n",
            "\tffffffffa68fd8e4 [unknown] ([unknown])\n",
            "\t000076cc4c45aa82 __poll+0x1f (/usr/lib/libc.so.6)\n",
            "\t00000000004a1b2c VTCP_read+0x9b (/work/prefix/sbin/vinyld)\n",
            "\n",
            "cache-epoll 2 3.0: 144 cpu/cycles/P:\n",
            "\t00000000004a0000 vwe_thread+0x10 (/work/prefix/sbin/vinyld)\n",
        ]
        self.assertEqual(
            list(fold(script)),
            [
                "cache-worker;VTCP_read;__poll;[kernel]",
                "cache-epoll;vwe_thread",
            ],
        )


if __name__ == "__main__":
    unittest.main()
