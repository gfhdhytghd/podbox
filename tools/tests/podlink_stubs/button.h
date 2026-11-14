#define BUTTON_MENU 1
int button_get(bool block);

#define BUTTON_REDRAW 0x20000000
void button_queue_post(long id, intptr_t data);
