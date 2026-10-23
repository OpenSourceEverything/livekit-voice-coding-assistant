/* Synthetic example only: no employer code or hardware claims. */
#include <stddef.h>
#include <stdint.h>

static uint8_t uart5_dma_buffer[256];

size_t uart5_dma_transmit(const uint8_t *data, size_t length) {
    if (data == NULL || length > sizeof(uart5_dma_buffer)) {
        return 0;
    }
    for (size_t i = 0; i < length; ++i) {
        uart5_dma_buffer[i] = data[i];
    }
    /* Actual hardware DMA is intentionally outside this desktop fixture. */
    return length;
}
