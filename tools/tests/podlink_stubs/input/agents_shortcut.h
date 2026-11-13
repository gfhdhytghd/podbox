/* Offline ports for the native shortcut. GPL version 2+. */
#define AGENTS_SHORTCUT_BUTTON 0x20000041
#define TIMEOUT_NOBLOCK 0
int agents_shortcut_get_button(int timeout);
void root_menu_agents_shortcut(void);

#define REMOTE_WPS_BUTTON 0x20000100
void root_menu_wps_shortcut(void);
