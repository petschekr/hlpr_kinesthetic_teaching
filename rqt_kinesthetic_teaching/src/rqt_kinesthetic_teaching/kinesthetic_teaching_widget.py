# -*- coding: UTF-8 -*-
import cPickle
import os
import signal
import threading
import time
import unicodedata

import rosbag
import rospkg
import rospy
from hlpr_kinesthetic_interaction.srv import KinestheticInteract
from hlpr_record_demonstration.msg import RecordKeyframeDemoAction
from python_qt_binding import loadUi
from python_qt_binding.QtCore import Qt, Signal, qWarning
from python_qt_binding.QtGui import (QComboBox, QFileDialog, QGraphicsView,
                                     QHeaderView, QIcon, QMessageBox,
                                     QTreeWidgetItem, QWidget)

from keyframe_bag_interface import KeyframeBagInterface, ParseException
from rqt_kinesthetic_interaction import (RQTKinestheticInteraction,
                                         TimeoutException)


class KinestheticTeachingWidget(QWidget):
    """
    Widget for use for kinesthetic teaching demos
    Handles all widget callbacks
    """

    STATUS_DISPLAY_TIME = 3 # seconds

    def __init__(self, context):
        super(KinestheticTeachingWidget, self).__init__()
        ui_file = os.path.join(rospkg.RosPack().get_path('rqt_kinesthetic_teaching'), 'resource', 'KinestheticTeaching.ui')
        loadUi(ui_file, self)

        self.setObjectName('KTeachingUi')
        self.setWindowTitle(self.windowTitle() + (' (%d)' % context.serial_number()))
        # Add widget to the user interface
        context.add_widget(self)

        # Set icons for buttons because they don't persist frm Qt creator for some reason
        self.demoLocationSaveButton.setIcon(QIcon.fromTheme("document-save-as"))
        self.demoLocationButton.setIcon(QIcon.fromTheme("document-open"))
        self.demoFolderLocationButton.setIcon(QIcon.fromTheme("folder"))
        self.playDemoButton.setIcon(QIcon.fromTheme("media-playback-start"))
        self.previewArmButton.setIcon(QIcon.fromTheme("view-fullscreen"))

        # Attach event handlers
        self.demoLocationButton.clicked[bool].connect(self.browseForLocation)
        self.demoFolderLocationButton.clicked[bool].connect(self.browseForFolder)
        self.demoLocationSaveButton.clicked[bool].connect(self.newLocation)
        self.demoLocation.returnPressed.connect(self.loadLocation)
        self.startTrajectoryButton.clicked[bool].connect(self.startTrajectory)
        self.startButton.clicked[bool].connect(self.startKeyframe)
        self.addButton.clicked[bool].connect(self.addKeyframe)
        self.openHandButton.clicked[bool].connect(self.openHand)
        self.closeHandButton.clicked[bool].connect(self.closeHand)
        self.endButton.clicked[bool].connect(self.endKeyframe)
        self.playDemoButton.clicked[bool].connect(self.playDemo)
        self.previewArmButton.clicked[bool].connect(self.previewDemo)
        self.enableKIBox.stateChanged.connect(self.enableKI)
        self.locateObjectsBox.stateChanged.connect(self.locateObjectsChanged)

        # Set sizing options for tree widget headers
        self.playbackTree.header().setStretchLastSection(False)
        self.playbackTree.header().setResizeMode(0, QHeaderView.Stretch)
        self.playbackTree.header().setResizeMode(1, QHeaderView.ResizeToContents)

        self.zeroMarker.setInsertPolicy(QComboBox.InsertAlphabetically)

        # Initialize the demonstration recorder
        self.kinesthetic_interaction = None
        try:
            self.kinesthetic_interaction = RQTKinestheticInteraction()
            print "Waiting for RQT kinesthetic interaction service"
            rospy.wait_for_service("kinesthetic_interaction")
            self.enable_kinesthetic_service = rospy.ServiceProxy("kinesthetic_interaction", KinestheticInteract)
            self.enable_kinesthetic_service(True)

            # Register callbacks
            self.kinesthetic_interaction.start_trajectory_cb = self.startTrajectoryCallback
            self.kinesthetic_interaction.start_keyframe_cb = self.startKeyframeCallback
            self.kinesthetic_interaction.add_keyframe_cb = self.addKeyframeCallback
            self.kinesthetic_interaction.end_keyframe_cb = self.endKeyframeCallback
        except TimeoutException as err:
            self._showWarning("Record keyframe demo server unreachable", str(err))
        self.keyframeBagInterface = None

    def enableKI(self, state):
        enabled = state != 0
        self.enable_kinesthetic_service(enabled)

    def _showWarning(self, title, body):
        if threading.current_thread().name != "MainThread":
            rospy.logwarn("{}: {}".format(title, body))
        else:
            msg = QMessageBox()
            msg.setIcon(QMessageBox.Warning)
            msg.setWindowTitle(title)
            msg.setText(body)
            msg.setStandardButtons(QMessageBox.Ok)
            msg.exec_()
    def _showStatus(self, text):
        self.status.setText(text)
        self.previousStatusText = text
        threading.Timer(self.STATUS_DISPLAY_TIME, self._expireStatus).start()
    def _expireStatus(self):
        if self.status.text() == self.previousStatusText:
            self.status.setText("Ready.")

    def browseForLocation(self):
        location = QFileDialog.getOpenFileName(filter="*.bag *.pkl;;*", directory=os.path.dirname(self.demoLocation.text()))[0]
        if len(location) == 0:
            return

        self.demoLocation.setText(location)
        self.loadLocation()
    def browseForFolder(self):
        location = QFileDialog.getExistingDirectory(directory=os.path.dirname(self.demoLocation.text()))
        if len(location) == 0:
            return

        self.demoLocation.setText(location)
        self.loadLocation()
    def _parseLocation(self, location, partOfFolder = False):
        items = []
        splitPath = os.path.splitext(location)
        if splitPath[1] == ".pkl":
            # Pickle file created previously by playback_demo_action_server_refactor
            with open(location, "rb") as pickleFile:
                pickleData = cPickle.load(pickleFile)
                playbackObjects = pickleData["playback_objects"]
                for playbackObject in playbackObjects:
                    item = QTreeWidgetItem()
                    title = "(Plan for #{}) {}".format(playbackObject.keyframe_num, time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(playbackObject.time_stamp.to_time())))
                    item.setText(0, title)
                    
                    jointFlagItem = QTreeWidgetItem()
                    jointFlagItem.setText(0, "Type")
                    jointFlagItem.setText(1, "Joint space" if playbackObject.joint_flag else "EEF space")
                    item.addChild(jointFlagItem)

                    targetItem = QTreeWidgetItem()
                    targetItem.setText(0, "Target")
                    if playbackObject.joint_flag:
                        targetItems = playbackObject.target
                    else:
                        targetItems = {
                            "orientation_w": playbackObject.target.orientation.w,
                            "orientation_x": playbackObject.target.orientation.x,
                            "orientation_y": playbackObject.target.orientation.y,
                            "orientation_z": playbackObject.target.orientation.z,
                            "position_x": playbackObject.target.position.x,
                            "position_y": playbackObject.target.position.y,
                            "position_z": playbackObject.target.position.z
                        }
                    for key, value in sorted(targetItems.items()):
                        subitem = QTreeWidgetItem()
                        subitem.setText(0, key)
                        subitem.setText(1, str(value))
                        targetItem.addChild(subitem)
                    item.addChild(targetItem)
                    items.append(item)

                # self.plan = None
                # self.target = None
                # self.gripper_val = None
                # self.keyframe_num = -1
                # self.time_stamp = None
                # self.joint_flag = None # True - it is joint space, False = EEF space 
        else:
            # Traditional .bag file
            try:
                self.keyframeBagInterface = KeyframeBagInterface()
                parsedData = self.keyframeBagInterface.parse(location)
                objectsInScene = self.keyframeBagInterface.parseContainedObjects(location)
            except (rosbag.bag.ROSBagException, ParseException) as err:
                self._showStatus(str(err))
                rospy.logwarn("[%s]: %s", location, str(err))
                self.playbackTree.clear()
                return

            objectLabels = []
            for item in objectsInScene:
                label = item.label
                if objectLabels.count(label) > 0:
                    label = "{} #{}".format(label, objectLabels.count(label) + 1)
                objectLabels.append(item.label)

                if partOfFolder:
                    self.zeroMarker.addItem(u"{} → {}".format(label, os.path.basename(location)))
                else:
                    self.zeroMarker.addItem(label)

            for i, keyframe in enumerate(parsedData):
                item = QTreeWidgetItem()
                title = "(#{}) ".format(i) + time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(keyframe["time"]))
                item.setText(0, title)
                # Add children
                for topic in sorted(keyframe["data"]):
                    data = keyframe["data"][topic]
                    topicItem = QTreeWidgetItem()
                    topicItem.setText(0, topic)
                    for attribute in sorted(data):
                        attributeValueItem = QTreeWidgetItem()
                        attributeValueItem.setText(0, attribute)
                        attributeValueItem.setText(1, str(data[attribute]))
                        topicItem.addChild(attributeValueItem)
                    item.addChild(topicItem)
                items.append(item)
        return items
    def loadLocation(self):
        self.startTrajectoryButton.setEnabled(False)
        self.startButton.setEnabled(False)
        self.addButton.setEnabled(False)
        self.openHandButton.setEnabled(False)
        self.closeHandButton.setEnabled(False)
        self.endButton.setEnabled(False)

        location = self.demoLocation.text()
        if os.path.isdir(location):
            locations = [os.path.join(location, f) for f in os.listdir(location) if os.path.isfile(os.path.join(location, f)) and (f.split(".")[-1] in ["bag", "pkl"])]
        else:
            locations = [location]

        if len(locations) == 0 or len(locations[0]) == 0:
            return
        
        self.keyframeCount.setText("")
        self.playbackTree.clear()
        self.zeroMarker.clear()
        self._showStatus("Parsing...")
        
        totalFrames = 0
        for location in sorted(locations):
            splitPath = os.path.splitext(location)
            bagLocation = None
            if splitPath[1] == ".pkl":
                bagLocation = splitPath[0] + ".bag"
                if not os.path.isfile(bagLocation):
                    bagLocation = "_".join(splitPath[0].split("_")[:-1]) + ".bag"
                if not os.path.isfile(bagLocation):
                    bagLocation = None
                    rospy.logwarn("%s has no corresponding .bag file. Detailed keyframe data can not be displayed.", location)
                
            items = self._parseLocation(location, len(locations) > 1)
            if bagLocation is not None:
                bagItems = self._parseLocation(bagLocation, len(locations) > 1)
                bagItemsHeader = QTreeWidgetItem()
                bagItemsHeader.setText(0, "Keyframe data ({})".format(bagLocation))
                bagItemsHeader.addChildren(bagItems)
                items.append(bagItemsHeader)

            totalFrames = len(items)
            if len(locations) == 1:
                self.playbackTree.addTopLevelItems(items)
            else:
                item = QTreeWidgetItem()
                item.setText(0, location)
                item.addChildren(items)
                self.playbackTree.addTopLevelItem(item)
        
        if len(locations) == 1:
            self.demoName.setText(os.path.basename(locations[0]))
            self.keyframeCount.setText("{} keyframe(s) loaded".format(totalFrames))
        else:
            self.demoName.setText(os.path.basename(self.demoLocation.text()) + os.path.sep)
            self.keyframeCount.setText("{} keyframe(s) loaded from {} files".format(totalFrames, len(locations)))
        self._showStatus("Parsed {} keyframe(s).".format(totalFrames))
        self.playDemoButton.setEnabled(True)
        self.previewArmButton.setEnabled(True)
        if self.locateObjectsBox.isChecked():
            self.zeroMarker.setEnabled(True)
    
    def newLocation(self):
        location = QFileDialog.getSaveFileName(filter="*.bag;;*", directory=os.path.dirname(self.demoLocation.text()))[0]
        if len(location) == 0:
            return
        self.demoLocation.setText(location)
        self.playDemoButton.setEnabled(False)
        self.previewArmButton.setEnabled(False)
        self.zeroMarker.clear()
        self.zeroMarker.setEnabled(False)
        self.startTrajectoryButton.setEnabled(True)
        self.startButton.setEnabled(True)
        self.addButton.setEnabled(True)
        self.openHandButton.setEnabled(True)
        self.closeHandButton.setEnabled(True)
        self.endButton.setEnabled(True)

        try:
            os.remove(location)
            self._showStatus("Deleted existing save file.")
        except OSError:
            pass

        if not self.kinesthetic_interaction:
            self._showWarning("Record keyframe demo server unreachable", "Record keyframe demo server isn't loaded. Run `roslaunch hlpr_record_demonstration start_record_services.launch` and restart the GUI.")
            return

        saveFile = self.kinesthetic_interaction.init_demo(location=location, timestamp=self.shouldTimestamp.isChecked())
        self.demoLocation.setText(saveFile)
        self.demoName.setText(os.path.basename(saveFile))
        self.keyframeCount.setText("")
        self.playbackTree.clear()

    def _keyframeRecorded(self):
        item = QTreeWidgetItem()
        title = "(#{}) ".format(self.playbackTree.topLevelItemCount()) + time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(time.time()))
        item.setText(0, title)
        self.playbackTree.addTopLevelItem(item)
        self.playbackTree.scrollToItem(item)
        self.keyframeCount.setText("{} keyframe(s) recorded".format(self.playbackTree.topLevelItemCount()))

    def locateObjectsChanged(self):
        self.kinesthetic_interaction.should_locate_objects = self.locateObjectsBox.isChecked()
        if self.playDemoButton.isEnabled():
            self.zeroMarker.setEnabled(self.locateObjectsBox.isChecked())
    
    def _generalStartActions(self):
        self.startTrajectoryButton.setEnabled(False)
        self.startButton.setEnabled(False)

        if self.kinesthetic_interaction.should_locate_objects:
            self._showStatus("Locating objects in scene...");
        
        print "Saving to {}".format(self.kinesthetic_interaction.demonstration.filename)

    def startTrajectory(self):
        self._generalStartActions()
        try:
            self.kinesthetic_interaction.demonstration_start_trajectory(None)
        except rospy.service.ServiceException:
            self._showWarning("Object location unavailable", "The object_location service is unavailable and is required when \"Locate objects\" is checked. Launch it with `roslaunch object_location object_location_engine.launch`.")
    def startTrajectoryCallback(self, success):
        if not success:
            self._showWarning("Could not start recording", "Failed to start trajectory recording. A recording is already in progress.")
            self.startTrajectoryButton.setEnabled(True)
        else:
            self.keyframeCount.setText("")
            self.playbackTree.clear()
            self._keyframeRecorded()
            self._showStatus("Trajectory started.")

    def startKeyframe(self):
        self._generalStartActions()
        try:
            self.kinesthetic_interaction.demonstration_start(None)
        except rospy.service.ServiceException:
            self._showWarning("Object location unavailable", "The object_location service is unavailable and is required when \"Locate objects\" is checked. Launch it with `roslaunch object_location object_location_engine.launch`.")
    def startKeyframeCallback(self, success):
        if not success:
            self.startButton.setEnabled(True)
            self._showWarning("Could not start recording", "Failed to start keyframe recording. A recording is already in progress.")
        else:
            self.keyframeCount.setText("")
            self.playbackTree.clear()
            self._keyframeRecorded()
            self._showStatus("Keyframe recording started.")
    
    def addKeyframe(self):
        self.kinesthetic_interaction.demonstration_keyframe(None)
    def addKeyframeCallback(self, success):
        if not success:
            self._showWarning("Could not record keyframe", "Failed to record keyframe. A recording is not currently in progress.")
        else:
            self._keyframeRecorded()
            self._showStatus("Keyframe recorded.")

    def openHand(self):
        self.kinesthetic_interaction._open_hand()
    def closeHand(self):
        self.kinesthetic_interaction._close_hand()

    def endKeyframe(self):
        self.kinesthetic_interaction.demonstration_end(None)
    def endKeyframeCallback(self, success):
        if not success:
            if not self.kinesthetic_interaction.demonstration.recording:
                text = "Failed to end recording. A recording is currently not in progress."
            else:
                text = "Failed to end recording for an unknown reason. Check the logs for more information."
            self._showWarning("Could not end recording", text)
        else:
            self._keyframeRecorded()
            if threading.current_thread().name == "MainThread":
                self._showStatus("Recording saved.")
                self.loadLocation()
            else:
                self._showStatus("Recording saved. Please open file manually.")
                rospy.logwarn("Recording saved but cannot be opened automatically because the end keyframe was written from a different thread. Please open the .bag file manually.")
                rospy.loginfo("Recording saved to {}".format(self.kinesthetic_interaction.demonstration.filename))

    def previewDemo(self):
        self.playDemo(qtArg=None, visualize="only")
    def _playDemoHandler(self, signum, frame):
        msg = "Could not load playback keyframe demo server. Run `roslaunch hlpr_record_demonstration start_playback_services.launch`."
        rospy.logerr(msg)
        raise TimeoutException(msg)
    def playDemo(self, qtArg, visualize="true"):
        location = self.demoLocation.text()
        self.keyframeBagInterface = KeyframeBagInterface()

        signal.signal(signal.SIGALRM, self._playDemoHandler)
        signal.alarm(2)
        try:
            self.keyframeBagInterface.playInit()
        except TimeoutException as err:
            self._showWarning("Playback keyframe demo server unreachable", str(err))
            return
        signal.alarm(0)

        if os.path.isdir(location):
            selected = self.playbackTree.selectedItems()
            if not selected or selected[0].parent():
                self._showWarning("Not playable", "Please select a keyframe sequence or preprocessed .pkl file to play.")
                return
            location = selected[0].text(0)

        try:
            self.kinesthetic_interaction._locate_objects()
        except rospy.service.ServiceException:
            self._showWarning("Object location unavailable", "The object_location service is unavailable and is required when \"Locate objects\" is checked. Launch it with `roslaunch object_location object_location_engine.launch`.")
            return

        self._showStatus("Playing...")
        rospy.loginfo("Playing {}".format(location))

        zeroMarker = self.zeroMarker.currentText() if self.kinesthetic_interaction.should_locate_objects else None
        if zeroMarker is not None and os.path.isdir(self.demoLocation.text()):
            zeroMarker = zeroMarker.split(u" → ")[0]

        self.keyframeBagInterface.play(self._deunicode(location), self._deunicode(zeroMarker), visualize, self.playDemoDone)
    def playDemoDone(self, feedback):
        print(feedback)
    
    def _deunicode(self, string):
        if string is not None:
            return unicodedata.normalize("NFKD", string).encode("ascii", "ignore")
        else:
            return None
