import tempfile

from twisted.internet.defer import succeed

from landscape.monitor.mountinfo import MountInfo
from landscape.tests.test_hal import MockHALManager, MockRealHALDevice
from landscape.tests.helpers import LandscapeTest, mock_counter, MonitorHelper
from landscape.tests.mocker import ANY


mb = lambda x: x * 1024 * 1024


class MountInfoTest(LandscapeTest):
    """Tests for mount-info plugin."""

    helpers = [MonitorHelper]

    def setUp(self):
        LandscapeTest.setUp(self)
        self.mstore.set_accepted_types(["mount-info", "mount-activity",
                                        "free-space"])

    def get_mount_info(self, *args, **kwargs):
        hal_devices = kwargs.pop("hal_devices", [])
        kwargs["hal_manager"] = MockHALManager(hal_devices)
        if "statvfs" not in kwargs:
            kwargs["statvfs"] = lambda path: (0,)*10
        return MountInfo(*args, **kwargs)

    def test_read_proc_mounts(self):
        """
        When the mount info plugin runs it reads data from
        /proc/mounts to discover mounts and calls os.statvfs() to
        retrieve current data for each mount point.  This test makes
        sure that os.statvfs() is called without failing, that
        /proc/mounts is readable, and that messages with the expected
        datatypes are generated.
        """
        plugin = self.get_mount_info(create_time=self.reactor.time)
        self.monitor.add(plugin)

        self.reactor.advance(self.monitor.step_size)

        message = plugin.create_mount_info_message()
        self.assertTrue(message)
        self.assertEquals(message["type"], "mount-info")
        self.assertTrue("mount-info" in message)
        self.assertTrue(len(message["mount-info"]) > 0)

        keys = set(["filesystem", "total-space", "device", "mount-point"])
        for now, mount_info in message["mount-info"]:
            self.assertEquals(set(mount_info.keys()), keys)
            self.assertTrue(isinstance(mount_info["filesystem"], basestring))
            self.assertTrue(isinstance(mount_info["device"], basestring))
            self.assertTrue(isinstance(mount_info["total-space"], (int, long)))
            self.assertTrue(isinstance(mount_info["mount-point"], basestring))

    def test_read_sample_data(self):
        """
        Sample data is used to ensure that the free space included in
        the message is calculated correctly.
        """
        def statvfs(path):
            if path == "/":
                return (4096, 0, mb(1000L), mb(100L), 0L, 0L, 0L, 0, 0)
            else:
                return (4096, 0, mb(10000L), mb(1000L), 0L, 0L, 0L, 0, 0)

        filename = self.makeFile("""\
rootfs / rootfs rw 0 0
none /dev ramfs rw 0 0
/dev/hda1 / ext3 rw 0 0
/dev/hda1 /dev/.static/dev ext3 rw 0 0
proc /proc proc rw,nodiratime 0 0
sysfs /sys sysfs rw 0 0
usbfs /proc/bus/usb usbfs rw 0 0
devpts /dev/pts devpts rw 0 0
tmpfs /dev/shm tmpfs rw 0 0
tmpfs /lib/modules/2.6.12-10-386/volatile tmpfs rw 0 0
/dev/hde1 /mnt/hde1 reiserfs rw 0 0
/dev/hde1 /mnt/bind reiserfs rw 0 0
/dev/sdb2 /media/Boot\\040OSX hfsplus nls=utf8 0 0
""")

        mtab_filename = self.makeFile("""\
rootfs / rootfs rw 0 0
none /dev ramfs rw 0 0
/dev/hda1 / ext3 rw 0 0
/dev/hda1 /dev/.static/dev ext3 rw 0 0
proc /proc proc rw,nodiratime 0 0
sysfs /sys sysfs rw 0 0
usbfs /proc/bus/usb usbfs rw 0 0
devpts /dev/pts devpts rw 0 0
tmpfs /dev/shm tmpfs rw 0 0
tmpfs /lib/modules/2.6.12-10-386/volatile tmpfs rw 0 0
/dev/hde1 /mnt/hde1 reiserfs rw 0 0
/dev/hde1 /mnt/bind none rw,bind 0 0
/dev/sdb2 /media/Boot\\040OSX hfsplus rw 0 0
""")

        plugin = self.get_mount_info(mounts_file=filename, statvfs=statvfs,
                                     create_time=self.reactor.time,
                                     mtab_file=mtab_filename)
        self.monitor.add(plugin)
        self.reactor.advance(self.monitor.step_size)

        message = plugin.create_mount_info_message()
        self.assertTrue(message)
        self.assertEquals(message["type"], "mount-info")

        mount_info = message.get("mount-info", ())

        self.assertEquals(len(mount_info), 3)

        self.assertEquals(mount_info[0][1],
                          {"device": "/dev/hda1", "mount-point": "/",
                           "filesystem": "ext3", "total-space": 4096000})

        self.assertEquals(mount_info[1][1],
                          {"device": "/dev/hde1", "mount-point": "/mnt/hde1",
                           "filesystem": "reiserfs", "total-space": 40960000})

        self.assertEquals(mount_info[2][1],
                          {"device": "/dev/sdb2", "mount-point": "/media/Boot OSX",
                           "filesystem": "hfsplus", "total-space": 40960000})

    def test_read_changing_total_space(self):
        """
        Total space measurements are only sent when (a) none have ever
        been sent, or (b) the value has changed since the last time
        data was collected.  The test sets the mount info plugin
        interval to the same value as the step size and advances the
        reactor such that the plugin will be run twice.  Each time it
        runs it gets a different value from our sample statvfs()
        function which should cause it to queue new messages.
        """
        def statvfs(path, multiplier=mock_counter(1).next):
            return (4096, 0, mb(multiplier() * 1000), mb(100), 0, 0, 0, 0, 0)

        filename = self.makeFile("""\
/dev/hda1 / ext3 rw 0 0
""")
        plugin = self.get_mount_info(mounts_file=filename, statvfs=statvfs,
                                     create_time=self.reactor.time,
                                     interval=self.monitor.step_size,
                                     mtab_file=filename)
        self.monitor.add(plugin)

        self.reactor.advance(self.monitor.step_size * 2)

        message = plugin.create_mount_info_message()
        mount_info = message["mount-info"]
        self.assertEquals(len(mount_info), 2)

        for i, total_space in enumerate([4096000, 8192000]):
            self.assertEquals(mount_info[i][0], (i+1) * self.monitor.step_size)
            self.assertEquals(mount_info[i][1],
                              {"device": "/dev/hda1", "filesystem": "ext3",
                               "mount-point": "/", "total-space": total_space})

    def test_read_disjointed_changing_total_space(self):
        """
        Total space measurements are only sent when (a) none have ever
        been sent, or (b) the value has changed since the last time
        data was collected.  This test ensures that the (b) criteria
        is checked per-mount point.  The sample statvfs() function
        only provides changing total space for /; therefore, new
        messages should only be queued for / after the first message
        is created.
        """
        def statvfs(path, multiplier=mock_counter(1).next):
            if path == "/":
                return (4096, 0, mb(1000), mb(100), 0, 0, 0, 0, 0)
            return (4096, 0, mb(multiplier() * 1000), mb(100), 0, 0, 0, 0, 0)

        filename = self.makeFile("""\
/dev/hda1 / ext3 rw 0 0
/dev/hde1 /mnt/hde1 ext3 rw 0 0
""")
        plugin = self.get_mount_info(mounts_file=filename, statvfs=statvfs,
                                     create_time=self.reactor.time,
                                     interval=self.monitor.step_size,
                                     mtab_file=filename)
        self.monitor.add(plugin)

        self.reactor.advance(self.monitor.step_size * 2)

        message = plugin.create_mount_info_message()
        self.assertTrue(message)

        mount_info = message.get("mount-info", ())
        self.assertEquals(len(mount_info), 3)

        self.assertEquals(mount_info[0][0], self.monitor.step_size)
        self.assertEquals(mount_info[0][1],
                          {"device": "/dev/hda1", "mount-point": "/",
                           "filesystem": "ext3", "total-space": 4096000})

        self.assertEquals(mount_info[1][0], self.monitor.step_size)
        self.assertEquals(mount_info[1][1],
                          {"device": "/dev/hde1", "mount-point": "/mnt/hde1",
                           "filesystem": "ext3", "total-space": 4096000})

        self.assertEquals(mount_info[2][0], self.monitor.step_size * 2)
        self.assertEquals(mount_info[2][1],
                          {"device": "/dev/hde1", "mount-point": "/mnt/hde1",
                           "filesystem": "ext3", "total-space": 8192000})

    def test_exchange_messages(self):
        """
        The mount_info plugin queues message when manager.exchange()
        is called.  Each message should be aligned to a step boundary;
        messages collected bewteen exchange periods should be
        delivered in a single message.
        """
        def statvfs(path):
            return (4096, 0, mb(1000), mb(100), 0, 0, 0, 0, 0)

        filename = self.makeFile("""\
/dev/hda1 / ext3 rw 0 0
""")
        plugin = self.get_mount_info(mounts_file=filename, statvfs=statvfs,
                                     create_time=self.reactor.time,
                                     mtab_file=filename)
        step_size = self.monitor.step_size
        self.monitor.add(plugin)

        # Exchange should trigger a flush of the persist database
        registry_mocker = self.mocker.replace(plugin.registry)
        registry_mocker.flush()
        self.mocker.result(None)
        self.mocker.replay()

        self.reactor.advance(step_size * 2)
        self.monitor.exchange()

        messages = self.mstore.get_pending_messages()
        self.assertEquals(len(messages), 3)

        message = [d for d in messages if d["type"] == "free-space"][0]
        free_space = message["free-space"]
        for i in range(len(free_space)):
            self.assertEquals(free_space[i][0], (i + 1) * step_size)
            self.assertEquals(free_space[i][1], "/")
            self.assertEquals(free_space[i][2], 409600)

    def test_messaging_flushes(self):
        """
        Duplicate message should never be created.  If no data is
        available, None will be returned when messages are created.
        """
        def statvfs(path):
            return (4096, 0, mb(1000), mb(100), 0, 0, 0, 0, 0)

        filename = self.makeFile("""\
/dev/hda1 / ext3 rw 0 0
""")
        plugin = self.get_mount_info(mounts_file=filename, statvfs=statvfs,
                                     create_time=self.reactor.time,
                                     mtab_file=filename)
        self.monitor.add(plugin)

        self.reactor.advance(self.monitor.step_size)

        messages = plugin.create_messages()
        self.assertEquals(len(messages), 3)

        messages = plugin.create_messages()
        self.assertEquals(len(messages), 0)

    def test_read_multi_bound_mounts(self):
        """
        The mount info plugin should handle multi-bound mount points
        by reporting them only once.  In practice, this test doesn't
        really test anything since the current behaviour is to ignore
        any mount point for which the device doesn't start with /dev.
        """
        def statvfs(path):
            return (4096, 0, mb(1000), mb(100), 0, 0, 0, 0, 0)

        filename = self.makeFile("""\
/dev/hdc4 /mm xfs rw 0 0
/mm/ubuntu-mirror /home/dchroot/warty/mirror none bind 0 0
/mm/ubuntu-mirror /home/dchroot/hoary/mirror none bind 0 0
/mm/ubuntu-mirror /home/dchroot/breezy/mirror none bind 0 0
""")
        plugin = self.get_mount_info(mounts_file=filename, statvfs=statvfs,
                                     create_time=self.reactor.time,
                                     mtab_file=filename)
        step_size = self.monitor.step_size
        self.monitor.add(plugin)

        self.reactor.advance(step_size)

        message = plugin.create_mount_info_message()
        self.assertTrue(message)

        mount_info = message.get("mount-info", ())
        self.assertEquals(len(mount_info), 1)

        self.assertEquals(mount_info[0][0], step_size)
        self.assertEquals(mount_info[0][1],
                          {"device": "/dev/hdc4", "mount-point": "/mm",
                           "filesystem": "xfs", "total-space": 4096000})

    def test_ignore_nfs_mounts(self):
        """
        The mount info plugin should only report data about local
        mount points.
        """

        filename = self.makeFile("""\
ennui:/data /data nfs rw,v3,rsize=32768,wsize=32768,hard,lock,proto=udp,addr=ennui 0 0
""")
        plugin = self.get_mount_info(mounts_file=filename, mtab_file=filename)
        self.monitor.add(plugin)
        plugin.run()

        message = plugin.create_mount_info_message()
        self.assertEquals(message, None)

    def test_ignore_removable_partitions(self):
        """
        Partitions on removable devices don't directly report
        storage.removable : True, but they do point to their parent and the
        parent will be marked removable if appropriate.
        """
        devices = [MockRealHALDevice({"info.udi": "wubble",
                                      "block.device": "/dev/scd",
                                      "storage.removable": True}),
                   MockRealHALDevice({"info.udi": "wubble0",
                                      "block.device": "/dev/scd0",
                                      "info.parent": "wubble"}),]

        filename = self.makeFile("""\
/dev/scd0 /media/Xerox_M750 iso9660 ro,nosuid,nodev,uid=1000,utf8 0 0
""")
        plugin = self.get_mount_info(mounts_file=filename, hal_devices=devices,
                                     mtab_file=filename)
        self.monitor.add(plugin)
        plugin.run()

        message = plugin.create_mount_info_message()
        self.assertEquals(message, None)

    def test_ignore_removable_devices(self):
        """
        The mount info plugin should only report data about
        non-removable devices.
        """
        devices = [MockRealHALDevice({"info.udi": "wubble",
                                      "block.device": "/dev/scd0",
                                      "storage.removable": True}),]
        filename = self.makeFile("""\
/dev/scd0 /media/Xerox_M750 iso9660 ro,nosuid,nodev,uid=1000,utf8 0 0
""")
        plugin = self.get_mount_info(mounts_file=filename, hal_devices=devices,
                                     mtab_file=filename)
        self.monitor.add(plugin)
        plugin.run()

        message = plugin.create_mount_info_message()
        self.assertEquals(message, None)

    def test_ignore_multiparented_removable_devices(self):
        """
        Some removable devices might be the grand-children of a device that is
        marked as "storage.removable".
        """
        devices = [MockRealHALDevice({"info.udi": "wubble",
                                      "block.device": "/dev/scd",
                                      "storage.removable": True}),
                   MockRealHALDevice({"info.udi": "wubble0",
                                      "block.device": "/dev/scd0",
                                      "info.parent": "wubble"}),
                   MockRealHALDevice({"info.udi": "wubble0a",
                                      "block.device": "/dev/scd0a",
                                      "info.parent": "wubble0"}),
                   MockRealHALDevice({"info.udi": "wubble0b",
                                      "block.device": "/dev/scd0b",
                                      "info.parent": "wubble0"}),]


        filename = self.makeFile("""\
/dev/scd0a /media/Xerox_M750 iso9660 ro,nosuid,nodev,uid=1000,utf8 0 0
""")
        plugin = self.get_mount_info(mounts_file=filename, hal_devices=devices,
                                     mtab_file=filename)
        self.monitor.add(plugin)
        plugin.run()

        message = plugin.create_mount_info_message()
        self.assertEquals(message, None)

    def test_sample_free_space(self):
        """Test collecting information about free space."""
        def statvfs(path, multiplier=mock_counter(1).next):
            return (4096, 0, mb(1000), mb(multiplier() * 100), 0, 0, 0, 0, 0)

        filename = self.makeFile("""\
/dev/hda2 / xfs rw 0 0
""")
        plugin = self.get_mount_info(mounts_file=filename, statvfs=statvfs,
                                     create_time=self.reactor.time,
                                     mtab_file=filename)
        step_size = self.monitor.step_size
        self.monitor.add(plugin)

        self.reactor.advance(step_size)

        message = plugin.create_free_space_message()
        self.assertTrue(message)
        self.assertEquals(message.get("type"), "free-space")
        free_space = message.get("free-space", ())
        self.assertEquals(len(free_space), 1)
        self.assertEquals(free_space[0], (step_size, "/", 409600))

    def test_never_exchange_empty_messages(self):
        """
        When the plugin has no data, it's various create_X_message()
        methods will return None.  Empty or null messages should never
        be queued.
        """
        self.mstore.set_accepted_types(["load-average"])

        filename = self.makeFile("")
        plugin = self.get_mount_info(mounts_file=filename, mtab_file=filename)
        self.monitor.add(plugin)
        self.monitor.exchange()
        self.assertEquals(len(self.mstore.get_pending_messages()), 0)

    def test_messages(self):
        """
        Test ensures all expected messages are created and contain the
        right datatypes.
        """
        def statvfs(path):
            return (4096, 0, mb(1000), mb(100), 0, 0, 0, 0, 0)

        filename = self.makeFile("""\
/dev/hda2 / xfs rw 0 0
""")
        plugin = self.get_mount_info(mounts_file=filename, statvfs=statvfs,
                                     create_time=self.reactor.time,
                                     mtab_file=filename)
        step_size = self.monitor.step_size
        self.monitor.add(plugin)

        self.reactor.advance(step_size)
        self.monitor.exchange()

        messages = self.mstore.get_pending_messages()
        self.assertEquals(len(messages), 3)
        self.assertEquals(messages[0].get("mount-info"),
                          [(step_size,
                            {"device": "/dev/hda2", "mount-point": "/",
                             "filesystem": "xfs", "total-space": 4096000})])
        self.assertEquals(messages[1].get("free-space"),
                          [(step_size, "/", 409600)])
        self.assertTrue(isinstance(messages[1]["free-space"][0][2],
                                   (int, long)))
        self.assertEquals(messages[2].get("activities"),
                          [(step_size, "/", True)])

    def test_first_mount_activity_message(self):
        """
        Mount activity is only reported when a change from the
        previous known state is detected.  If mount activity has never
        been reported, it should be.
        """
        filename = self.makeFile("""\
/dev/hda2 / xfs rw 0 0
""")
        plugin = self.get_mount_info(mounts_file=filename,
                                     create_time=self.reactor.time,
                                     mtab_file=filename)
        step_size = self.monitor.step_size
        self.monitor.add(plugin)

        self.reactor.advance(step_size)
        message = plugin.create_mount_activity_message()
        self.assertEquals(message.get("type"), "mount-activity")
        self.assertEquals(message.get("activities"), [(300, "/", True)])

        self.reactor.advance(step_size)
        self.assertEquals(plugin.create_mount_activity_message(), None)

    def test_wb_umount_activity(self):
        """Test ensures the plugin reports new umounts."""
        filename = self.makeFile("""\
/dev/hda2 / xfs rw 0 0
""")
        plugin = self.get_mount_info(mounts_file=filename,
                                     create_time=self.reactor.time,
                                     mtab_file=filename)
        step_size = self.monitor.step_size
        self.monitor.add(plugin)

        self.reactor.advance(step_size)
        message = plugin.create_mount_activity_message()
        self.assertEquals(message.get("type"), "mount-activity")
        self.assertEquals(message.get("activities"), [(step_size, "/", True)])

        plugin._mounts_file = self.makeFile("""\
""")
        self.reactor.advance(step_size)
        message = plugin.create_mount_activity_message()
        self.assertEquals(message.get("type"), "mount-activity")
        self.assertEquals(message.get("activities"),
                          [(step_size * 2, "/", False)])

    def test_wb_mount_activity(self):
        """Test ensures the plugin reports new mounts."""
        filename = self.makeFile("""\
/dev/hda2 / xfs rw 0 0
""")
        plugin = self.get_mount_info(mounts_file=filename,
                                     create_time=self.reactor.time,
                                     mtab_file=filename)
        step_size = self.monitor.step_size
        self.monitor.add(plugin)

        self.reactor.advance(step_size)
        message = plugin.create_mount_activity_message()
        self.assertEquals(message.get("type"), "mount-activity")
        self.assertEquals(message.get("activities"), [(step_size, "/", True)])

        mount_dir = self.makeDir()
        plugin._mounts_file = self.makeFile("""\
/dev/hda2 / xfs rw 0 0
/dev/hdb5 %s xfs rw 0 0
""" % mount_dir)
        self.reactor.advance(step_size)
        message = plugin.create_mount_activity_message()
        self.assertEquals(message.get("type"), "mount-activity")
        self.assertEquals(message.get("activities"),
                          [(step_size * 2, mount_dir, True)])


    def test_resynchronize(self):
        """
        On the reactor "resynchronize" event, new mount-info messages
        should be sent.
        """
        def statvfs(path):
            return (4096, 0, mb(1000), mb(100), 0, 0, 0, 0, 0)
        filename = self.makeFile("""\
/dev/hda1 / ext3 rw 0 0
""")
        plugin = self.get_mount_info(mounts_file=filename,
                                     create_time=self.reactor.time,
                                     statvfs=statvfs, mtab_file=filename)
        self.monitor.add(plugin)

        plugin.run()
        plugin.exchange()
        self.reactor.fire("resynchronize")
        plugin.run()
        plugin.exchange()
        messages = self.mstore.get_pending_messages()
        messages = [message for message in messages
                    if message["type"] == "mount-info"]
        expected_message = {
            'type': 'mount-info',
            'mount-info': [(0, {'device': '/dev/hda1', 'mount-point': '/',
                                'total-space': 4096000, 'filesystem': 'ext3'})]}
        self.assertMessages(messages, [expected_message, expected_message])

    def test_bind_mounts(self):
        """
        Mounted devices that are mounted using Linux's "--bind" option
        shouldn't be listed, as they have the same free space/used space as the
        device they're bound to.
        """
        def statvfs(path):
            return (4096, 0, mb(1000), mb(100), 0, 0, 0, 0, 0)

        # From this test data, we expect only two mount points to be returned,
        # and the other two to be ignored (the rebound /dev/hda2 -> /mnt mounting)
        filename = self.makeFile("""\
/dev/devices/by-uuid/12345567 / ext3 rw 0 0
/dev/hda2 /usr ext3 rw 0 0
/dev/devices/by-uuid/12345567 /mnt ext3 rw 0 0
/dev/devices/by-uuid/12345567 /media/Boot\\040OSX hfsplus rw 0 0
""")

        mtab_filename = self.makeFile("""\
/dev/hda1 / ext3 rw 0 0
/dev/hda2 /usr ext3 rw 0 0
/opt /mnt none rw,bind 0 0
/opt /media/Boot\\040OSX none rw,bind 0 0
""")
        plugin = MountInfo(mounts_file=filename, create_time=self.reactor.time,
                           statvfs=statvfs, mtab_file=mtab_filename)
        self.monitor.add(plugin)
        plugin.run()
        message = plugin.create_mount_info_message()
        self.assertEquals(message.get("mount-info"),
            [(0, {"device": "/dev/devices/by-uuid/12345567",
                  "mount-point": "/", "total-space": 4096000L,
                  "filesystem": "ext3"}),
             (0 ,{"device": "/dev/hda2",
                  "mount-point": "/usr",
                  "total-space": 4096000L,
                  "filesystem": "ext3"}),
             ])

    def test_no_mtab_file(self):
        """
        If there's no mtab file available, then we can make no guesses about
        bind mounted directories, so any filesystems in /proc/mounts will be
        reported.
        """
        def statvfs(path):
            return (4096, 0, mb(1000), mb(100), 0, 0, 0, 0, 0)

        # In this test, we expect all mount points to be returned, as we can't
        # identify any as bind mounts.
        filename = self.makeFile("""\
/dev/devices/by-uuid/12345567 / ext3 rw 0 0
/dev/hda2 /usr ext3 rw 0 0
/dev/devices/by-uuid/12345567 /mnt ext3 rw 0 0
""")
        # mktemp isn't normally secure, due to race conditions, but in this
        # case, we don't actually create the file at all.
        mtab_filename = tempfile.mktemp()
        plugin = MountInfo(mounts_file=filename, create_time=self.reactor.time,
                           statvfs=statvfs, mtab_file=mtab_filename)
        self.monitor.add(plugin)
        plugin.run()
        message = plugin.create_mount_info_message()
        self.assertEquals(message.get("mount-info"),
            [(0, {"device": "/dev/devices/by-uuid/12345567",
                  "mount-point": "/", "total-space": 4096000L,
                  "filesystem": "ext3"}),
             (0,{"device": "/dev/hda2",
                 "mount-point": "/usr",
                 "total-space": 4096000L,
                 "filesystem": "ext3"}),
             (0,{"device": "/dev/devices/by-uuid/12345567",
                 "mount-point": "/mnt",
                 "total-space": 4096000L,
                 "filesystem": "ext3"}),])

    def test_no_message_if_not_accepted(self):
        """
        Don't add any messages at all if the broker isn't currently
        accepting their type.
        """
        self.mstore.set_accepted_types([])
        def statvfs(path):
            return (4096, 0, mb(1000), mb(100), 0, 0, 0, 0, 0)

        # From this test data, we expect only two mount points to be returned,
        # and the third to be ignored (the rebound /dev/hda2 -> /mnt mounting)
        filename = self.makeFile("""\
/dev/devices/by-uuid/12345567 / ext3 rw 0 0
/dev/hda2 /usr ext3 rw 0 0
/dev/devices/by-uuid/12345567 /mnt ext3 rw 0 0
""")

        mtab_filename = self.makeFile("""\
/dev/hda1 / ext3 rw 0 0
/dev/hda2 /usr ext3 rw 0 0
/opt /mnt none rw,bind 0 0
""")
        plugin = MountInfo(mounts_file=filename, create_time=self.reactor.time,
                           statvfs=statvfs, mtab_file=mtab_filename)
        self.monitor.add(plugin)
        self.reactor.advance(self.monitor.step_size * 2)
        self.monitor.exchange()

        self.mstore.set_accepted_types(["mount-info"])
        self.assertMessages(list(self.mstore.get_pending_messages()), [])

    def test_call_on_accepted(self):
        plugin = self.get_mount_info(create_time=self.reactor.time)
        self.monitor.add(plugin)

        self.reactor.advance(plugin.run_interval)

        remote_broker_mock = self.mocker.replace(self.remote)
        remote_broker_mock.send_message(ANY, urgent=True)
        self.mocker.result(succeed(None))
        self.mocker.count(3)
        self.mocker.replay()

        self.reactor.fire(("message-type-acceptance-changed", "mount-info"),
                          True)

    def test_persist_timing(self):
        """Mount info are only persisted when exchange happens.

        Previously mount info were persisted as soon as they were gathered: if
        an event happened between the persist and the exchange, the server
        didn't get the mount info at all. This test ensures that mount info are
        only saved when exchange happens.
        """
        def statvfs(path):
            return (4096, 0, mb(1000), mb(100), 0, 0, 0, 0, 0)

        filename = self.makeFile("""\
/dev/hda1 / ext3 rw 0 0
""")
        plugin = MountInfo(mounts_file=filename, create_time=self.reactor.time,
                           statvfs=statvfs, mtab_file=filename)
        self.monitor.add(plugin)
        plugin.run()
        message1 = plugin.create_mount_info_message()
        self.assertEquals(
            message1.get("mount-info"),
            [(0, {"device": "/dev/hda1",
                  "filesystem": "ext3",
                  "mount-point": "/",
                  "total-space": 4096000L})])
        plugin.run()
        message2 = plugin.create_mount_info_message()
        self.assertEquals(
            message2.get("mount-info"),
            [(0, {"device": "/dev/hda1",
                  "filesystem": "ext3",
                  "mount-point": "/",
                  "total-space": 4096000L})])
        # Run again, calling create_mount_info_message purge the information
        plugin.run()
        plugin.exchange()
        plugin.run()
        message3 = plugin.create_mount_info_message()
        self.assertIdentical(message3, None)

    def test_exchange_limits_exchanged_free_space_messages(self):
        """
        In order not to overload the server, the client should stagger the
        exchange of free-space messages.
        """
        def statvfs(path):
            return (4096, 0, mb(1000), mb(100), 0, 0, 0, 0, 0)

        filename = self.makeFile("""\
/dev/hda1 / ext3 rw 0 0
""")
        plugin = self.get_mount_info(mounts_file=filename, statvfs=statvfs,
                                     create_time=self.reactor.time,
                                     mtab_file=filename)
        # Limit the test exchange to 5 items.
        plugin.MAX_FREE_SPACE_ITEMS_TO_EXCHANGE = 5
        step_size = self.monitor.step_size
        self.monitor.add(plugin)

        # Exchange should trigger a flush of the persist database
        registry_mocker = self.mocker.replace(plugin.registry)
        registry_mocker.flush()
        self.mocker.result(None)
        self.mocker.replay()

        # Generate 10 data points
        self.reactor.advance(step_size * 10)
        self.monitor.exchange()

        messages = self.mstore.get_pending_messages()
        self.assertEquals(len(messages), 3)

        message = [d for d in messages if d["type"] == "free-space"][0]
        free_space = message["free-space"]
        free_space_items = len(free_space)
        self.assertEquals(free_space_items, 5)
        for i in range(free_space_items):
            self.assertEquals(free_space[i][0], (i + 1) * step_size)
            self.assertEquals(free_space[i][1], "/")
            self.assertEquals(free_space[i][2], 409600)

        # The second exchange should pick up the remaining items.
        self.mstore.delete_all_messages()
        self.monitor.exchange()

        messages = self.mstore.get_pending_messages()
        self.assertEquals(len(messages), 1)

        message = [d for d in messages if d["type"] == "free-space"][0]
        free_space = message["free-space"]
        free_space_items = len(free_space)
        self.assertEquals(free_space_items, 5)
        for i in range(free_space_items):
            # Note (i+6) we've already retrieved the first 5 items.
            self.assertEquals(free_space[i][0], (i + 6) * step_size)
            self.assertEquals(free_space[i][1], "/")
            self.assertEquals(free_space[i][2], 409600)

        # Third exchange should not get any further free-space messages
        self.mstore.delete_all_messages()
        self.monitor.exchange()
        messages = self.mstore.get_pending_messages()
        self.assertEquals(len(messages), 0)
