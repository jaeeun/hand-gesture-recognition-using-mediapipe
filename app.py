#!/usr/bin/env python
# -*- coding: utf-8 -*-
import csv
import copy
import argparse
import itertools
import cv2 as cv
import numpy as np
import mediapipe as mp
import datetime
import flexbuffers
import paho.mqtt.client as mqtt

from collections import Counter
from collections import deque
# from sys import platlibdir
from math import*
from utils import CvFpsCalc
from model import KeyPointClassifier
from model import PointHistoryClassifier

def on_connect(client, userdata, flags, rc):
    if rc==0:
        print('Connected OK')
    else:
        print('Bad connection Returned code = ',rc)

def on_disconnect(client, userdata, flags, rc=0):
    print(str(rc))

def on_publish(client, userdata, mid):
    blank=0
    # print('In on_pub callback mid = ',mid)

finger_elements = {
    "hand" : "right",
    "landmark" : "0",
}
gesture_elements = {
    "gesture" : "idle",
    "param1" : "0",
    "param2" : "0",
    "param3" : "0"
}

client = mqtt.Client()
client.on_connect = on_connect
client.on_disconnect = on_disconnect
client.on_publish = on_publish
client.connect('127.0.0.1', 1883)
client.loop_start()


def get_args():
    parser = argparse.ArgumentParser()

    parser.add_argument("--device", type=int, default=0)
    parser.add_argument("--width", help='cap width', type=int, default=960)
    parser.add_argument("--height", help='cap height', type=int, default=540)
    parser.add_argument("--number", help='keypoint number', type=int, default=0)

    parser.add_argument('--use_static_image_mode', action='store_true')
    parser.add_argument("--min_detection_confidence",
                        help='min_detection_confidence',
                        type=float,
                        default=0.7)
    parser.add_argument("--min_tracking_confidence",
                        help='min_tracking_confidence',
                        type=int,
                        default=0.5)

    args = parser.parse_args()

    return args


def main():
    args = get_args()

    cap_device = args.device
    cap_width = args.width
    cap_height = args.height
    cap_number = args.number

    use_static_image_mode = args.use_static_image_mode
    min_detection_confidence = args.min_detection_confidence
    min_tracking_confidence = args.min_tracking_confidence

    use_brect = True

    # カメラ準備 ###############################################################
    cap = cv.VideoCapture(cap_device)
    # cap = cv.VideoCapture("test_video/finger_walking_test1.mp4")
    cap.set(cv.CAP_PROP_FRAME_WIDTH, cap_width)
    cap.set(cv.CAP_PROP_FRAME_HEIGHT, cap_height)

    # モデルロード #############################################################
    mp_hands = mp.solutions.hands
    mp_drawing = mp.solutions.drawing_utils
    hands = mp_hands.Hands(
        static_image_mode=use_static_image_mode,
        max_num_hands=2,
        min_detection_confidence=min_detection_confidence,
        min_tracking_confidence=min_tracking_confidence,
    )

    keypoint_classifier = KeyPointClassifier()

    point_history_classifier = PointHistoryClassifier()

    # ラベル読み込み ###########################################################
    with open('model/keypoint_classifier/keypoint_classifier_label.csv',
              encoding='utf-8-sig') as f:
        keypoint_classifier_labels = csv.reader(f)
        keypoint_classifier_labels = [
            row[0] for row in keypoint_classifier_labels
        ]
    with open(
            'model/point_history_classifier/point_history_classifier_label.csv',
            encoding='utf-8-sig') as f:
        point_history_classifier_labels = csv.reader(f)
        point_history_classifier_labels = [
            row[0] for row in point_history_classifier_labels
        ]

    # FPS計測モジュール ########################################################
    cvFpsCalc = CvFpsCalc(buffer_len=10)

    # 座標履歴 #################################################################
    history_length = 16
    point_history = deque(maxlen=history_length)

    # フィンガージェスチャー履歴 ################################################
    finger_gesture_history = deque(maxlen=history_length)

    #  ########################################################################
    mode = 0
    cross = 0
    cross_pre = 0
    pre_time = datetime.datetime.now()
    last_point_gun = [0,0,0]

    while True:
        fps = cvFpsCalc.get()

        # キー処理(ESC：終了) #################################################
        key = cv.waitKey(10)
        if key == 27:  # ESC
            break
        number, mode = select_mode(key, mode)
        # mode = 1
        # number = cap_number
        degree = 0

        # 카메라 캡쳐 #####################################################
        ret, image = cap.read()
        if not ret:
            break
        image = cv.flip(image, 1)  # ミラー表示
        debug_image = copy.deepcopy(image)

        # 검출 #############################################################
        image = cv.cvtColor(image, cv.COLOR_BGR2RGB)

        image.flags.writeable = False
        results = hands.process(image)
        image.flags.writeable = True

        #  ####################################################################
        if results.multi_hand_landmarks is not None:
            for hand_landmarks, handedness in zip(results.multi_hand_landmarks,
                                                  results.multi_handedness):                                  
                mp_drawing.draw_landmarks(debug_image, hand_landmarks, mp_hands.HAND_CONNECTIONS)

                # calc_bounding_rect
                brect = calc_bounding_rect(debug_image, hand_landmarks)
                # 랜드마크
                landmark_list, landmark_3Dlist = calc_landmark_list(debug_image, hand_landmarks)

                # 상대 좌표, 정규화 좌표로의 변환
                pre_processed_landmark_list = pre_process_landmark(
                    landmark_list)
                pre_processed_point_history_list = pre_process_point_history(
                    debug_image, point_history)
                # 학습데이터 저장
                logging_csv(number, mode, pre_processed_landmark_list,
                            pre_processed_point_history_list)
                
                finger_elements["landmark"] = ""
                for landmark in landmark_3Dlist:
                    finger_elements["landmark"]=finger_elements["landmark"]+str(landmark[2])+','+str(landmark[0])+','+str(landmark[1])+','
                
                # print(finger_elements)

                fbb = flexbuffers.Builder()
                fbb.MapFromElements(finger_elements)
                data = fbb.Finish()
                client.publish("/finger",data,1)

                # sign 분류
                hand_sign_id = keypoint_classifier(pre_processed_landmark_list)

                # 포인팅
                if hand_sign_id == 2:
                    point_history.append(landmark_list[8])

                # 걷기 뛰기
                elif hand_sign_id == 5:
                    point_history.append(landmark_list[8])
                    point_history.append(landmark_list[12])

                    # print('fingers : '+str(landmark_list[8][1])+' , '+str(landmark_list[12][1]))
                    if landmark_list[8][1]>landmark_list[12][1]: cross=0
                    else: cross=1
                    if cross != cross_pre:
                        now = datetime.datetime.now()
                        diff = now-pre_time
                        pre_time = now
                        f_diff = diff.seconds + diff.microseconds/1000000
                        # print(int(50/f_diff))
                        if f_diff<1:
                            gesture_elements["gesture"]="Walking"
                            gesture_elements["param2"]=str(int(50/f_diff))

                            palm1 = int(landmark_3Dlist[5][2]/30)
                            palm2 = int(landmark_3Dlist[17][2]/30)

                            if palm1==palm2:
                                gesture_elements["param1"]="front"
                            elif palm1>palm2:
                                gesture_elements["param1"]="left"
                            else:
                                gesture_elements["param1"]="right"

                            # print(gesture_elements)

                            fbb = flexbuffers.Builder()
                            fbb.MapFromElements(gesture_elements)
                            data = fbb.Finish()
                            client.publish("/gesture",data,1)
                    cross_pre = cross

                # 총 쏘기
                elif hand_sign_id == 7 or hand_sign_id==6:
                    
                    v1 = [landmark_3Dlist[8][0]-landmark_3Dlist[7][0],landmark_3Dlist[8][1]-landmark_3Dlist[7][1],landmark_3Dlist[8][2]-landmark_3Dlist[7][2]]
                    v2 = [landmark_3Dlist[5][0]-landmark_3Dlist[6][0],landmark_3Dlist[5][1]-landmark_3Dlist[6][1],landmark_3Dlist[5][2]-landmark_3Dlist[6][2]]

                    v_in = v1[0]*v2[0] + v1[1]*v2[1] + v1[2]*v2[2]

                    s_v1=sqrt(pow(v1[0],2)+pow(v1[1],2)+pow(v1[2],2))
                    s_v2=sqrt(pow(v2[0],2)+pow(v2[1],2)+pow(v2[2],2))

                    degree = int(degrees(acos(v_in/(s_v1*s_v2))))
                    
                    out=[]
                    out.append(v1[1]*v2[2]-v1[2]*v2[1])
                    out.append(v1[2]*v2[0]-v1[0]*v2[2])
                    out.append(v1[0]*v2[1]-v1[1]*v2[0])
                    v_in = v1[0]*v2[0] + v1[1]*v2[1] + v1[2]*v2[2]

                    gesture_elements["gesture"]= "Gun"
                    gesture_elements["param1"] = str(landmark_list[8][0])+','+str(landmark_list[8][1])
                    gesture_elements["param2"] = str(degree)

                    cv.putText(debug_image, "degree:" + str(degree), (400, 30), cv.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0,0), 2, cv.LINE_AA)
                    cv.putText(debug_image, "degree:" + str(degree), (400, 30), cv.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2, cv.LINE_AA)       

                    fbb = flexbuffers.Builder()
                    fbb.MapFromElements(gesture_elements)
                    data = fbb.Finish()
                    client.publish("/gesture",data,1)

                    if degree<90:
                        now = datetime.datetime.now()
                        diff = now - pre_time
                        f_diff = diff.seconds + diff.microseconds/1000000
                        pre_time = now

                        if f_diff>1:
                            print("Shoot : "+str(degree))
                            gesture_elements["gesture"] = "Shoot"
                            gesture_elements["param1"] = str(last_point_gun[0])+','+str(last_point_gun[1])
                            gesture_elements["param2"] = str(degree)

                            print(gesture_elements)

                            fbb = flexbuffers.Builder()
                            fbb.MapFromElements(gesture_elements)
                            data = fbb.Finish()
                            client.publish("/gesture",data,1)
                        else:
                            last_point_gun = landmark_list[8]

                else:
                    point_history.append([0, 0])

                # 핑거 제스쳐 분류
                finger_gesture_id = 0
                point_history_len = len(pre_processed_point_history_list)
                if point_history_len == (history_length * 2):
                    finger_gesture_id = point_history_classifier(
                        pre_processed_point_history_list)

                # history에서 가장 많이 검출된 제스쳐 id
                finger_gesture_history.append(finger_gesture_id)
                most_common_fg_id = Counter(
                    finger_gesture_history).most_common()

                # drawing
                debug_image = draw_bounding_rect(use_brect, debug_image, brect)
                debug_image = draw_info_text(
                    debug_image,
                    brect,
                    handedness,
                    keypoint_classifier_labels[hand_sign_id],
                    point_history_classifier_labels[most_common_fg_id[0][0]],
                )
        else:
            point_history.append([0, 0])

        debug_image = draw_point_history(debug_image, point_history)
        debug_image = draw_info(debug_image, fps, mode, number)

        # image show #############################################################
        cv.imshow('Hand Gesture Recognition', debug_image)

    cap.release()
    cv.destroyAllWindows()


def select_mode(key, mode):
    number = -1
    if 48 <= key <= 57:  # 0 ~ 9
        number = key - 48
    if key == 110:  # n
        mode = 0
    if key == 107:  # k
        mode = 1
    if key == 104:  # h
        mode = 2
    return number, mode


def calc_bounding_rect(image, landmarks):
    image_width, image_height = image.shape[1], image.shape[0]

    landmark_array = np.empty((0, 2), int)

    for _, landmark in enumerate(landmarks.landmark):
        landmark_x = min(int(landmark.x * image_width), image_width - 1)
        landmark_y = min(int(landmark.y * image_height), image_height - 1)

        landmark_point = [np.array((landmark_x, landmark_y))]

        landmark_array = np.append(landmark_array, landmark_point, axis=0)

    x, y, w, h = cv.boundingRect(landmark_array)

    return [x, y, x + w, y + h]


def calc_landmark_list(image, landmarks):
    image_width, image_height = image.shape[1], image.shape[0]

    landmark_point = []
    landmark_3dpoint = []

    # 키 포인트
    for _, landmark in enumerate(landmarks.landmark):
        landmark_x = min(int(landmark.x * image_width), image_width - 1)
        landmark_y = min(int(landmark.y * image_height), image_height - 1)
        landmark_z = landmark.z * image_height

        landmark_point.append([landmark_x, landmark_y])
        landmark_3dpoint.append([landmark_x, landmark_y, landmark_z])

    return landmark_point,landmark_3dpoint


def pre_process_landmark(landmark_list):
    temp_landmark_list = copy.deepcopy(landmark_list)

    # 상대 좌표로 변환
    base_x, base_y = 0, 0
    for index, landmark_point in enumerate(temp_landmark_list):
        if index == 0:
            base_x, base_y = landmark_point[0], landmark_point[1]

        temp_landmark_list[index][0] = temp_landmark_list[index][0] - base_x
        temp_landmark_list[index][1] = temp_landmark_list[index][1] - base_y

    # 1次元リストに変換
    temp_landmark_list = list(
        itertools.chain.from_iterable(temp_landmark_list))

    # 正規化
    max_value = max(list(map(abs, temp_landmark_list)))

    def normalize_(n):
        return n / max_value

    temp_landmark_list = list(map(normalize_, temp_landmark_list))

    return temp_landmark_list


def pre_process_point_history(image, point_history):
    image_width, image_height = image.shape[1], image.shape[0]

    temp_point_history = copy.deepcopy(point_history)

    # 相対座標に変換
    base_x, base_y = 0, 0
    for index, point in enumerate(temp_point_history):
        if index == 0:
            base_x, base_y = point[0], point[1]

        temp_point_history[index][0] = (temp_point_history[index][0] -
                                        base_x) / image_width
        temp_point_history[index][1] = (temp_point_history[index][1] -
                                        base_y) / image_height

    # 1次元リストに変換
    temp_point_history = list(
        itertools.chain.from_iterable(temp_point_history))

    return temp_point_history


def logging_csv(number, mode, landmark_list, point_history_list):
    if mode == 0:
        pass
    if mode == 1 and (0 <= number <= 11):
        csv_path = 'model/keypoint_classifier/keypoint.csv'
        with open(csv_path, 'a', newline="") as f:
            writer = csv.writer(f)
            writer.writerow([number, *landmark_list])
    if mode == 2 and (0 <= number <= 9):
        csv_path = 'model/point_history_classifier/point_history.csv'
        with open(csv_path, 'a', newline="") as f:
            writer = csv.writer(f)
            writer.writerow([number, *point_history_list])
    return


def draw_bounding_rect(use_brect, image, brect):
    if use_brect:
        # 外接矩形
        cv.rectangle(image, (brect[0], brect[1]), (brect[2], brect[3]),
                     (0, 0, 0), 1)

    return image


def draw_info_text(image, brect, handedness, hand_sign_text,
                   finger_gesture_text):
    cv.rectangle(image, (brect[0], brect[1]), (brect[2], brect[1] - 22),
                 (0, 0, 0), -1)

    info_text = handedness.classification[0].label[0:]
    if hand_sign_text != "":
        info_text = info_text + ':' + hand_sign_text
    cv.putText(image, info_text, (brect[0] + 5, brect[1] - 4),
               cv.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1, cv.LINE_AA)

    if finger_gesture_text != "":
        cv.putText(image, "Finger Gesture:" + finger_gesture_text, (10, 60),
                   cv.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 0), 4, cv.LINE_AA)
        cv.putText(image, "Finger Gesture:" + finger_gesture_text, (10, 60),
                   cv.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2,
                   cv.LINE_AA)

    return image


def draw_point_history(image, point_history):
    for index, point in enumerate(point_history):
        if point[0] != 0 and point[1] != 0:
            cv.circle(image, (point[0], point[1]), 1 + int(index / 2),
                      (152, 251, 152), 2)

    return image


def draw_info(image, fps, mode, number):
    cv.putText(image, "FPS:" + str(fps), (10, 30), cv.FONT_HERSHEY_SIMPLEX,
               0.8, (0, 0, 0), 4, cv.LINE_AA)
    cv.putText(image, "FPS:" + str(fps), (10, 30), cv.FONT_HERSHEY_SIMPLEX,
               0.8, (255, 255, 255), 2, cv.LINE_AA)

    mode_string = ['Logging Key Point', 'Logging Point History']
    if 1 <= mode <= 2:
        cv.putText(image, "MODE:" + mode_string[mode - 1], (10, 90),
                   cv.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1,
                   cv.LINE_AA)
        if 0 <= number <= 9:
            cv.putText(image, "NUM:" + str(number), (10, 110),
                       cv.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1,
                       cv.LINE_AA)
    return image


if __name__ == '__main__':
    main()
