#include <stdint.h>
extern uint32_t test_gpio, test_pcon11, test_pcon12, test_pdat;
uint32_t *test_gpio_register(void);
#define GPIOCMD (*test_gpio_register())
#define PCON11 test_pcon11
#define PCON12 test_pcon12
#define PDAT(bank) test_pdat
