"""
RealSense depth camera obstacle detection.

Opens the camera on demand, captures one aligned frame, runs obstacle detection
(depth filter + dark color filter + contour detection), and returns structured
results with an annotated base64 JPEG image.
"""
from __future__ import annotations

import base64
import logging
import threading

import cv2
import numpy as np
import pyrealsense2 as rs

from .schemas import CameraScanResult, ObstacleDetail

logger = logging.getLogger(__name__)

# Prevent concurrent camera access
_camera_lock = threading.Lock()


def scan_for_obstacles() -> CameraScanResult:
    """Capture one RealSense frame and detect obstacles."""
    with _camera_lock:
        pipeline = rs.pipeline()
        config = rs.config()
        config.enable_stream(rs.stream.depth, 640, 480, rs.format.z16, 30)
        config.enable_stream(rs.stream.color, 640, 480, rs.format.bgr8, 30)

        started = False
        try:
            profile = pipeline.start(config)
            started = True
            depth_sensor = profile.get_device().first_depth_sensor()
            depth_scale = depth_sensor.get_depth_scale()
            align = rs.align(rs.stream.color)

            # Discard frames for auto-exposure warmup
            for _ in range(15):
                pipeline.wait_for_frames()

            frames = pipeline.wait_for_frames()
            aligned = align.process(frames)

            depth_frame = aligned.get_depth_frame()
            color_frame = aligned.get_color_frame()

            if not depth_frame or not color_frame:
                return CameraScanResult(detected=False)

            depth_image = np.asanyarray(depth_frame.get_data())
            color_image = np.asanyarray(color_frame.get_data())

            # Depth filter: 0.2m – 1.5m
            depth_m = depth_image * depth_scale
            depth_mask = cv2.inRange(depth_m, 0.2, 1.5)

            # Color filter: red objects via HSV
            hsv = cv2.cvtColor(color_image, cv2.COLOR_BGR2HSV)
            # Red wraps around hue 0/180, so use two ranges
            mask_low = cv2.inRange(hsv, (0, 70, 50), (10, 255, 255))
            mask_high = cv2.inRange(hsv, (170, 70, 50), (180, 255, 255))
            color_mask = cv2.bitwise_or(mask_low, mask_high)

            # Only look at the lower-left quadrant
            h_img, w_img = color_image.shape[:2]
            roi_mask = np.zeros((h_img, w_img), dtype=np.uint8)
            roi_mask[h_img // 2 :, : w_img // 2] = 255

            # Combine masks
            combined = cv2.bitwise_and(color_mask, depth_mask)
            combined = cv2.bitwise_and(combined, roi_mask)

            # Morphological cleanup
            kernel = np.ones((11, 11), np.uint8)
            combined = cv2.morphologyEx(combined, cv2.MORPH_CLOSE, kernel)
            combined = cv2.medianBlur(combined, 5)

            # Find contours
            contours, _ = cv2.findContours(
                combined, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
            )

            obstacles = []
            annotated = color_image.copy()

            for cnt in contours:
                area = cv2.contourArea(cnt)
                if area < 3000:
                    continue

                x, y, w, h = cv2.boundingRect(cnt)

                # Median depth of the ROI
                roi_depth = depth_m[y : y + h, x : x + w]
                valid = roi_depth[roi_depth > 0]
                distance = float(np.median(valid)) if valid.size > 0 else 0.0

                # Draw on annotated image
                cv2.rectangle(annotated, (x, y), (x + w, y + h), (0, 0, 255), 3)
                cv2.putText(
                    annotated,
                    f"Obstacle: {distance:.2f}m",
                    (x, y - 10),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.6,
                    (0, 0, 255),
                    2,
                )

                obstacles.append(
                    ObstacleDetail(
                        x=x, y=y, width=w, height=h,
                        distance_m=round(distance, 3),
                        area_px=int(area),
                    )
                )

            # Crop to lower-left quadrant (matches detection region)
            annotated = annotated[h_img // 2 :, : w_img // 2]

            # Encode annotated image as base64 JPEG
            _, jpeg_buf = cv2.imencode(
                ".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, 85]
            )
            image_b64 = base64.b64encode(jpeg_buf).decode("ascii")

            logger.info(
                f"Camera scan: {len(obstacles)} obstacle(s) detected"
            )

            return CameraScanResult(
                detected=len(obstacles) > 0,
                obstacle_count=len(obstacles),
                obstacles=obstacles,
                image_base64=image_b64,
            )
        finally:
            if started:
                pipeline.stop()
