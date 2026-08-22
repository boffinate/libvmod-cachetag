/*-
 * SPDX-License-Identifier: MPL-2.0
 *
 * Print the counter inventory held in cachetag_counters.h, one tab-separated
 * record per row in table order: name, type, level, oneliner.
 * cachetag_counter_parity.sh diffs this against src/cachetag.vsc and fails the
 * test suite if the two have drifted.
 *
 * Deliberately free of Vinyl headers and link dependencies: this is a
 * check-only program and the table is all it needs.
 */

#include <stdio.h>

#include "cachetag_counters.h"

#define CACHETAG_COUNTER_DUMP(n, t, l, o)				\
	printf("%s\t%s\t%s\t%s\n", #n, #t, #l, o);

int
main(void)
{

	CACHETAG_COUNTERS(CACHETAG_COUNTER_DUMP)
	return (0);
}

#undef CACHETAG_COUNTER_DUMP
