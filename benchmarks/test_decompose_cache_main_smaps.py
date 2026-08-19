#!/usr/bin/env python3
"""Focused synthetic-fixture tests for cache-main smaps decomposition."""

from __future__ import annotations

import unittest

from decompose_cache_main_smaps import SmapsParseError, decompose_mappings, parse_smaps


def mapping(start: int, perms: str, pathname: str, size: int, rss: int, pss: int) -> str:
    end = start + size * 1024
    header = f"{start:x}-{end:x} {perms} 00000000 08:01 1"
    if pathname:
        header += f" {pathname}"
    return "\n".join(
        [
            header,
            f"Size:                {size} kB",
            "KernelPageSize:        4 kB",
            f"Rss:                 {rss} kB",
            f"Pss:                 {pss} kB",
            "Shared_Clean:          0 kB",
            "Private_Dirty:         0 kB",
            "Anonymous:             0 kB",
            "THPeligible:           0",
            "VmFlags: rd wr mr mw me",
        ]
    )


FIXTURE = "\n".join(
    [
        mapping(0x1000, "rw-p", "[heap]", 100, 80, 70),
        mapping(0x2000, "rw-p", "", 50, 40, 40),
        mapping(0x3000, "rw-p", "[stack:42]", 20, 10, 10),
        mapping(0x4000, "r-xp", "/usr/sbin/vinyld", 30, 20, 15),
        mapping(0x5000, "r--p", "/opt/vinyl/lib/libvmod_cachetag.so", 40, 30, 20),
        mapping(0x6000, "rw-s", "/var/lib/vinyl/storage.bin", 60, 50, 40),
        mapping(0x7000, "r--p", "/usr/lib/x86_64-linux-gnu/libc.so.6", 70, 60, 50),
        mapping(0x8000, "rw-p", "/tmp/application.dat", 80, 70, 60),
    ]
)


class SmapsDecompositionTests(unittest.TestCase):
    def test_categories_conserve_mapping_size_rss_and_pss(self) -> None:
        result = decompose_mappings(parse_smaps(FIXTURE))

        self.assertEqual(result["schema"], "cache-main-smaps-decomposition-v1")
        self.assertEqual(result["mapping_count"], 8)
        self.assertTrue(result["all_checks_pass"])
        self.assertEqual(result["totals"], {"mappings": 8, "size_kb": 450, "rss_kb": 360, "pss_kb": 305})
        categories = result["categories"]
        self.assertEqual(categories["heap"]["pss_kb"], 70)
        self.assertEqual(categories["anonymous"]["pss_kb"], 40)
        self.assertEqual(categories["thread_stacks"]["pss_kb"], 10)
        self.assertEqual(categories["vinyl_cachetag"]["mappings"], 2)
        self.assertEqual(categories["storage_file"]["rss_kb"], 50)
        self.assertEqual(categories["executable_shared_libraries"]["mappings"], 1)
        self.assertEqual(categories["unknown_file_backed"]["mappings"], 1)

    def test_storage_marker_precedes_broad_vinyl_path(self) -> None:
        fixture = mapping(0x1000, "rw-s", "/tmp/vinyl-cache/storage-main", 4, 3, 2)
        result = decompose_mappings(parse_smaps(fixture))
        self.assertEqual(result["categories"]["storage_file"]["mappings"], 1)
        self.assertEqual(result["categories"]["vinyl_cachetag"]["mappings"], 0)

    def test_explicit_storage_pattern_classifies_unusual_filename(self) -> None:
        fixture = mapping(0x1000, "rw-s", "/mnt/cache/objects.bin", 4, 3, 2)
        result = decompose_mappings(parse_smaps(fixture), ("/mnt/cache/objects.bin",))
        self.assertEqual(result["categories"]["storage_file"]["mappings"], 1)

    def test_missing_required_metric_fails_closed(self) -> None:
        fixture = mapping(0x1000, "rw-p", "[heap]", 4, 3, 2).replace("Pss:                 2 kB\n", "")
        with self.assertRaises(SmapsParseError):
            parse_smaps(fixture)

    def test_pss_above_rss_fails_closed(self) -> None:
        with self.assertRaises(SmapsParseError):
            parse_smaps(mapping(0x1000, "rw-p", "[heap]", 4, 3, 4))

    def test_size_must_match_mapping_range(self) -> None:
        fixture = mapping(0x1000, "rw-p", "[heap]", 4, 3, 2).replace(
            "Size:                4 kB", "Size:                5 kB"
        )
        with self.assertRaises(SmapsParseError):
            parse_smaps(fixture)


if __name__ == "__main__":
    unittest.main()
