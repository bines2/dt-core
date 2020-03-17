#!/usr/bin/env python
import rospy

import numpy as np
import cv2
from cv_bridge import CvBridge, CvBridgeError
import scipy.fftpack

from duckietown import DTROS, DTPublisher, DTSubscriber
from duckietown_utils.bag_logs import numpy_from_ros_compressed
from duckietown_msgs.msg import Vector2D, LEDDetection, LEDDetectionArray,\
                                BoolStamped, SignalsDetection
from led_detection.LED_detector import LEDDetector
from sensor_msgs.msg import CompressedImage


class LEDDetectorNode(DTROS):
    def __init__(self, node_name):

        # Initialize the DTROS parent class
        super(LEDDetectorNode, self).__init__(node_name=node_name)

        # Needed to publish images
        self.bridge = CvBridge()

        # Add the node parameters to the parameters dictionary
        self.parameters['~capture_time'] = None
        self.parameters['~DTOL'] = None
        self.parameters['~useFFT'] = None
        self.parameters['~freqIdentity'] = None
        self.parameters['~crop_params'] = None
        self.parameters['~blob_detector_db'] = {}
        self.parameters['~blob_detector_tl'] = {}
        self.parameters['~verbose'] = None
        self.parameters['~cell_size'] = None
        self.parameters['~LED_protocol'] = None

        # Initialize detector
        self.detector = LEDDetector(self.parameters, self.log)

        # To trigger the first change, we set this manually
        self.parameterChanged = True
        self.updateParameters()

        self.first_timestamp = 0
        self.capture_finished = True
        self.t_init = None
        self.trigger = True
        self.node_state = 0
        self.data = []

        # Initialize detection
        self.right = None
        self.front = None
        self.traffic_light = None
        # We currently are not able to see what happens on the left
        self.left = "UNKNOWN"

        # Publishers
        self.pub_detections = DTPublisher("~signals_detection", SignalsDetection, queue_size=1)

        # Publishers for debug images
        self.pub_image_right = DTPublisher("~image_detection_right/compressed", CompressedImage, queue_size=1)
        self.pub_image_front = DTPublisher("~image_detection_front/compressed", CompressedImage, queue_size=1)
        self.pub_image_TL = DTPublisher("~image_detection_TL/compressed", CompressedImage, queue_size=1)

        # Subscribers
        self.sub_cam = DTSubscriber("~image/compressed", CompressedImage, self.camera_callback)

        # Log info
        self.log('Initialized!')

    def camera_callback(self, msg):

        float_time = msg.header.stamp.to_sec()

        if self.trigger:
            self.trigger = False
            self.data = []
            self.capture_finished = False
            # Start capturing images
            self.first_timestamp = msg.header.stamp.to_sec()
            self.t_init = rospy.Time.now().to_sec()

        elif self.capture_finished:
            self.node_state = 0

        if self.first_timestamp > 0:
            rel_time = float_time - self.first_timestamp

            # Capturing
            if rel_time < self.parameters['~capture_time']:
                self.node_state = 1
                # Capture image
                rgb = numpy_from_ros_compressed(msg)
                rgb = cv2.cvtColor(rgb, cv2.COLOR_BGRA2GRAY)
                rgb = 255 - rgb
                self.data.append({'timestamp': float_time, 'rgb': rgb[:, :]})

            # Start processing
            elif not self.capture_finished and self.first_timestamp > 0:
                if self.parameters['~verbose'] == 2:
                    self.log('Relative Time %s, processing' % rel_time)
                self.node_state = 2
                self.capture_finished = True
                self.first_timestamp = 0

                # IMPORTANT! Explicitly ignore messages while processing, accumulates delay otherwise!
                self.sub_cam.unregister()

                # Process image and publish results
                self.process_and_publish()

    def crop_image(self, images, crop_norm):
        # Get size
        height, width, _ = images.shape
        # Compute indices
        h_start = int(np.floor(height * crop_norm[0][0]))
        h_end = int(np.ceil(height * crop_norm[0][1]))
        w_start = int(np.floor(width * crop_norm[1][0]))
        w_end = int(np.ceil(width * crop_norm[1][1]))
        # Crop image
        image_cropped = images[h_start:h_end, w_start:w_end, :]
        # Return cropped image
        return image_cropped


    def process_and_publish(self):
        # Initial time
        tic = rospy.Time.now().to_sec()

        # Get dimensions
        h, w = self.data[0]['rgb'].shape
        num_img = len(self.data)

        # Save images in numpy arrays
        images = np.zeros((h, w, num_img), dtype=np.uint8)
        timestamps = np.zeros(num_img)
        for i, v in enumerate(self.data):
            timestamps[i] = v['timestamp']
            images[:, :, i] = v['rgb']

        # Crop images
        img_right = self.crop_image(images, self.parameters['~crop_params']['cropNormalizedRight'])
        img_front = self.crop_image(images, self.parameters['~crop_params']['cropNormalizedFront'])
        img_tl = self.crop_image(images, self.parameters['~crop_params']['cropNormalizedTL'])

        # Print on screen
        if self.parameters['~verbose'] == 2:
            self.log('Analyzing %s images of size %s X %s' % (num_img, w, h))

        # Get blobs right
        blobs_right, frame_right = self.detector.find_blobs(img_right, 'car')
        # Get blobs front
        blobs_front, frame_front = self.detector.find_blobs(img_front, 'car')
        # Get blobs right
        blobs_tl, frame_tl = self.detector.find_blobs(img_tl, 'tl')

        radius = self.parameters['~DTOL']/2.0

        if self.parameters['~verbose'] > 0:
            # Extract blobs for visualization
            keypoint_blob_right = self.detector.get_keypoints(blobs_right, radius)
            keypoint_blob_front = self.detector.get_keypoints(blobs_front, radius)
            keypoint_blob_tl = self.detector.get_keypoints(blobs_tl, radius)

            # Images
            img_pub_right = cv2.drawKeypoints(img_right[:, :, -1], keypoint_blob_right, np.array([]), (0, 0, 255),
                                              cv2.DRAW_MATCHES_FLAGS_DRAW_RICH_KEYPOINTS)
            img_pub_front = cv2.drawKeypoints(img_front[:, :, -1], keypoint_blob_front, np.array([]), (0, 0, 255),
                                              cv2.DRAW_MATCHES_FLAGS_DRAW_RICH_KEYPOINTS)
            img_pub_tl = cv2.drawKeypoints(img_tl[:, :, -1], keypoint_blob_tl, np.array([]), (0, 0, 255),
                                           cv2.DRAW_MATCHES_FLAGS_DRAW_RICH_KEYPOINTS)
        else:
            img_pub_right = None
            img_pub_front = None
            img_pub_tl = None

        # Initialize detection
        self.right = None
        self.front = None
        self.traffic_light = None

        # Sampling time
        t_s = (1.0*self.parameters['~capture_time'])/(1.0*num_img)

        # Decide whether LED or not
        self.right = self.detector.interpret_signal(blobs_right, t_s, num_img)
        self.front = self.detector.interpret_signal(blobs_front, t_s, num_img)
        self.traffic_light = self.detector.interpret_signal(blobs_tl, t_s, num_img)

        # Left bot (also UNKNOWN)
        self.left = "UNKNOWN"

        # Final time
        processing_time = rospy.Time.now().to_sec() - tic
        total_time = rospy.Time.now().to_sec() - self.t_init

        # Publish results
        self.publish(img_pub_right, img_pub_front, img_pub_tl)

        # Print performance
        if self.parameters['~verbose'] == 2:
            self.log('[%s] Detection completed. Processing time: %.2f s. Total time:  %.2f s' %
                     (self.node_name, processing_time, total_time))

        # Keep going
        self.trigger = True
        self.sub_cam = DTSubscriber("~image/compressed", CompressedImage, self.camera_callback)

    def publish(self, img_right, img_front, img_tl):
        #  Publish image with circles if verbose is > 0
        if self.parameters['~verbose'] > 0:
            img_right_circle_msg = self.bridge.cv2_to_compressed_imgmsg(img_right) # , encoding="bgr8")
            img_front_circle_msg = self.bridge.cv2_to_compressed_imgmsg(img_front) # , encoding="bgr8")
            img_tl_circle_msg = self.bridge.cv2_to_compressed_imgmsg(img_tl) # , encoding="bgr8")

            # Publish image
            self.pub_image_right.publish(img_right_circle_msg)
            self.pub_image_front.publish(img_front_circle_msg)
            self.pub_image_TL.publish(img_tl_circle_msg)

        # Log results to the terminal
        self.log("The observed LEDs are:\n Front = %s\n Right = %s\n Traffic light state = %s" %
                 (self.front, self.right, self.traffic_light))

        # Publish detections
        detections_msg = SignalsDetection(front=self.front,
                                          right=self.right,
                                          left=self.left,
                                          traffic_light_state=self.traffic_light)
        self.pub_detections.publish(detections_msg)

    def cbParametersChanged(self):
        """Updates parameters."""
        super(LEDDetectorNode, self).cbParametersChanged()
        if self.parameterChanged:
            self.detector.update_parameters(self.parameters)

            self.parameterChanged = False


if __name__ == '__main__':
    # Initialize the node
    led_detector_node = LEDDetectorNode(node_name='led_detector_node')
    # Keep it spinning to keep the node alive
    rospy.spin()
