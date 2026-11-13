/* Accessory volume keys while Agents is open. GPL version 2+. */
#ifndef IAP_AGENTS_VOLUME_H
#define IAP_AGENTS_VOLUME_H
#include <stdbool.h>
void iap_agents_volume_enable(bool enabled);
bool iap_agents_volume(unsigned char level, bool mute);
bool iap_agents_volume_get(unsigned char *level, bool *mute);
int iap_agents_volume_take(void);
#endif
