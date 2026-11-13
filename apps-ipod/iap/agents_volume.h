/* Accessory volume keys while Agents is open. GPL version 2+. */
#ifndef IAP_AGENTS_VOLUME_H
#define IAP_AGENTS_VOLUME_H
#include <stdbool.h>
/* A provisional bounded wait, shorter than one ~108 ms IR interval. */
#define AGENTS_VOLUME_ARBITRATION_MS 80
#define AGENTS_VOLUME_STEPS 64
void iap_agents_volume_enable(bool enabled);
bool iap_agents_volume(unsigned char level, bool mute);
bool iap_agents_volume_get(unsigned char *level, bool *mute);
int iap_agents_volume_take(void);
int iap_agents_volume_timeout(int timeout);
#endif
