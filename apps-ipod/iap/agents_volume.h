/* Remote volume sources for WPS audio and non-WPS navigation. GPL version 2+. */
#ifndef IAP_AGENTS_VOLUME_H
#define IAP_AGENTS_VOLUME_H
#include <stdbool.h>
#define AGENTS_VOLUME_STEPS 64
/* Publish the UI input context; only the actual WPS allows audio volume. */
void iap_volume_context(bool wps);
int iap_volume_filter(int button);
void iap_agents_volume_enable(bool enabled);
/* Connected view delegates PC volume to host keys; disconnected paging stays. */
void iap_agents_volume_host_keys(bool host_keys);
bool iap_agents_volume(unsigned char level, bool mute);
bool iap_agents_volume_get(unsigned char *level, bool *mute);
int iap_agents_volume_take(void);
#endif
