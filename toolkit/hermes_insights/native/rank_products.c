/* Optional original MT19937/Fisher-Yates implementation; no CPython source
 * copied. Python supplies its seeded state and retains math.fsum and Pearson.
 * Build explicitly with build_native_stats.py, never during a request. */
#include <stdint.h>
#include <stddef.h>
#include <stdlib.h>
#include <string.h>
#include <float.h>
#include <limits.h>

#ifndef OHA_SOURCE_BUILD
#error "Use scripts/build_native_stats.py to record the source/build identity"
#endif

#define MAX_VALUES 65536
#define MAX_PRODUCTS 262144
#define MAX_DRAWS 128

unsigned oha_rank_abi(void) {
    return (CHAR_BIT == 8 && sizeof(double) == 8 && FLT_RADIX == 2 &&
            DBL_MANT_DIG == 53 && DBL_MAX_EXP == 1024) ? 1 : 0;
}
unsigned oha_pointer_bits(void) { return sizeof(void *) * CHAR_BIT; }
const char *oha_source_build(void) { return OHA_SOURCE_BUILD; }

static uint32_t next_word(uint32_t *state) {
    if (state[624] == 624) {
        for (size_t i = 0; i < 624; ++i) {
            uint32_t mix = (state[i] & UINT32_C(0x80000000)) |
                (state[(i + 1) % 624] & UINT32_C(0x7fffffff));
            state[i] = state[(i + 397) % 624] ^ (mix >> 1) ^
                ((mix & 1) ? UINT32_C(0x9908b0df) : 0);
        }
        state[624] = 0;
    }
    uint32_t word = state[state[624]++];
    word ^= word >> 11;
    word ^= (word << 7) & UINT32_C(0x9d2c5680);
    word ^= (word << 15) & UINT32_C(0xefc60000);
    return word ^ (word >> 18);
}

int oha_rank_products(uint32_t *state, const double *left, const double *right,
                      size_t size, size_t draws, double *products,
                      size_t capacity) {
    if (!state || !left || !right || !products || state[624] > 624 ||
        size < 2 || size > MAX_VALUES || draws == 0 || draws > MAX_DRAWS ||
        capacity > MAX_PRODUCTS || draws > capacity / size ||
        size > SIZE_MAX / sizeof(double) || size > SIZE_MAX / sizeof(unsigned) ||
        capacity > SIZE_MAX / sizeof(double)) return 1;
    double *labels = malloc(size * sizeof(double));
    unsigned *bits = malloc(size * sizeof(unsigned));
    if (!labels || !bits) { free(labels); free(bits); return 2; }
    for (size_t i = 1; i < size; ++i) {
        unsigned count = 0;
        for (size_t bound = i + 1; bound; bound >>= 1) ++count;
        bits[i] = count;
    }
    for (size_t draw = 0; draw < draws; ++draw) {
        memcpy(labels, right, size * sizeof(double));
        for (size_t i = size - 1; i > 0; --i) {
            uint32_t selected;
            do { selected = next_word(state) >> (32 - bits[i]); }
            while (selected >= i + 1);
            double saved = labels[i];
            labels[i] = labels[selected];
            labels[selected] = saved;
        }
        for (size_t i = 0; i < size; ++i)
            products[draw * size + i] = left[i] * labels[i];
    }
    free(bits);
    free(labels);
    return 0;
}
