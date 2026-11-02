/* Include the committed harness to reuse only its RAM controller. */
#define main committed_test_main
#include "podlink_firmware_test.c"
#undef main
int main(void)
{
    usb_serial_init();
    assert(podlink_open());
    struct podlink_message m;
    insert();
    poll_message(&m);
    input_frame(PODLINK_OPEN, 11, hello, sizeof(hello));
    for (int i = 0; i < 20; ++i)
        poll_message(&m);
    assert(podlink_state() == PODLINK_CONNECTED);
    clear_output();
    unsigned char partial[PODLINK_FRAME_MAX];
    unsigned char payload[4096] = {0};
    podlink_encode(partial, PODLINK_BINARY, 12, payload, sizeof(payload));
    input_bytes(partial, 32);
    poll_message(&m);
    assert(parser.used == 32);
    /* Pending application TX and buffered input belong to the old stream. */
    assert(podlink_send(PODLINK_TEXT, "stale", 5) == PODLINK_SENT);
    assert(podlink_send(PODLINK_BINARY, payload, sizeof(payload)) ==
           PODLINK_SENT);
    /* The core disconnects then initializes in one SET_CONFIGURATION call. */
    usb_serial_disconnect();
    recv_ptr = NULL;
    sent_size = 0;
    assert(usb_serial_init_connection() == 0);
    input_frame(PODLINK_OPEN, 100, hello, sizeof(hello));
    assert(poll_message(&m) == 1 && m.type == PODLINK_CLOSE);
    assert(m.length == 2 && m.data[0] == (1006 & 255));
    assert(podlink_state() == PODLINK_WAIT_USB);
    assert(count == 0 && input_pos == 0 && input_length == 0);
    assert(parser.used == 0 && !ping_pending);
    for (int i = 0; i < 20; ++i)
        poll_message(&m);
    printf("After reconfiguration: state=%d parser.used=%u output_bytes=%d\n",
           podlink_state(), parser.used, output_size);
    struct podlink_parser decoded = {0};
    int hellos = 0, opens = 0;
    for (int i = 0; i < output_size; ++i)
    {
        int rc = podlink_decode(&decoded, output[i], &m);
        assert(rc >= 0);
        if (rc == 1)
        {
            if (m.type == PODLINK_HELLO)
                ++hellos;
            else
            {
                assert(m.type == PODLINK_OPEN && m.seq == 100);
                ++opens;
            }
        }
    }
    assert(hellos == 1 && opens == 1);
    assert(podlink_state() == PODLINK_CONNECTED);
    puts("PASS: reconfiguration resets partial RX and pending TX without a UI "
         "poll");
    return 0;
}
