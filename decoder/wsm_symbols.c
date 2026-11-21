/*
  wsm_symbols.c - prints the 162 channel symbols (0..3, one digit each) of a WSPR message, e.g.  wsm_symbols "PU2TKN HI03 10"
  Used by the test signal generator (wsm_testsignal.py). Encoder from the wsprd sources (K1JT / K9AN), GPL v3.
*/
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "wsprd/wsprd.h"
#include "wsprd/wsprsim_utils.h"

int main(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr, "usage: %s \"CALL GRID DBM\"\n", argv[0]);
        return 2;
    }
    static char hashtab[HASHTAB_SIZE * HASHTAB_ENTRY_LEN];
    static char loctab[HASHTAB_SIZE * LOCTAB_ENTRY_LEN];
    unsigned char sym[162];
    char msg[64];
    snprintf(msg, sizeof(msg), "%s", argv[1]);
    if (!get_wspr_channel_symbols(msg, hashtab, loctab, sym)) {
        fprintf(stderr, "cannot encode message\n");
        return 1;
    }
    for (int i = 0; i < 162; i++) putchar('0' + sym[i]);
    putchar('\n');
    return 0;
}
