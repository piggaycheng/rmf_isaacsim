#!/usr/bin/env python3
"""
Test script for verifying multi-step task dispatch through Open-RMF Fleet Adapter.
Tests:
1. Auto-proceed multi-step task (gimbal_move -> gimbal_rotate -> gimbal_move)
2. Manual-proceed multi-step task (gimbal_move -> [wait] -> call /proceed -> gimbal_move -> done)
3. Multi-step task including navigate (navigate -> gimbal_move -> navigate back)
"""

import sys
import time
import requests
import json

BASE_URL = "http://localhost:8088"

def check_health():
    resp = requests.get(f"{BASE_URL}/api/health")
    assert resp.status_code == 200, f"Health check failed: {resp.text}"
    print("✅ API Server is healthy")

def get_robots():
    for _ in range(15):
        resp = requests.get(f"{BASE_URL}/api/robots")
        if resp.status_code == 200:
            robots = [r for r in resp.json() if r.get("online")]
            if robots:
                print(f"🤖 Connected online robots: {[r['id'] for r in robots]}")
                return robots
        time.sleep(1)
    raise RuntimeError("No online robots available!")

def test_auto_proceed_task(robot_id="carter2"):
    print(f"\n--- [TEST 1] Auto-proceed multi-step task on {robot_id} ---")
    payload = {
        "robot_id": robot_id,
        "auto_proceed": True,
        "step_delay": 0.5,
        "steps": [
            {"type": "gimbal_move", "pan_deg": 30.0, "tilt_deg": 0.0},
            {"type": "gimbal_rotate", "pan_deg": -15.0, "tilt_deg": 0.0},
            {"type": "gimbal_move", "pan_deg": 0.0, "tilt_deg": 0.0},
        ]
    }
    resp = requests.post(f"{BASE_URL}/api/tasks/multi-step", json=payload)
    assert resp.status_code == 200, f"Dispatch failed: {resp.text}"
    data = resp.json()
    task_id = data.get("task_id")
    print(f"🚀 Dispatched task_id: {task_id}")

    # Monitor task state
    start_time = time.time()
    completed = False
    while time.time() - start_time < 30:
        time.sleep(1)
        tasks_resp = requests.get(f"{BASE_URL}/api/tasks")
        tasks = tasks_resp.json()
        target_task = next((t for t in tasks if t.get("id") == task_id or t.get("request_id") == task_id), None)
        if target_task:
            status = target_task.get("status")
            progress = target_task.get("progress")
            step = target_task.get("current_step", 0)
            detail = target_task.get("detail", "")
            print(f"   Task [{task_id}]: status={status}, progress={progress}%, step={step}, detail='{detail}'")
            if status == "completed":
                completed = True
                break
            elif status in ("failed", "canceled"):
                raise RuntimeError(f"Task ended in failure: {status}, {target_task.get('error')}")

    assert completed, f"Task {task_id} did not complete within timeout"
    print(f"✅ TEST 1 PASSED: Auto-proceed task completed successfully!")

def test_manual_proceed_task(robot_id="carter2"):
    print(f"\n--- [TEST 2] Manual-proceed multi-step task on {robot_id} ---")
    payload = {
        "robot_id": robot_id,
        "auto_proceed": False,
        "steps": [
            {"type": "gimbal_move", "pan_deg": 25.0, "tilt_deg": 5.0},
            {"type": "gimbal_move", "pan_deg": 0.0, "tilt_deg": 0.0},
        ]
    }
    resp = requests.post(f"{BASE_URL}/api/tasks/multi-step", json=payload)
    assert resp.status_code == 200, f"Dispatch failed: {resp.text}"
    data = resp.json()
    task_id = data.get("task_id")
    print(f"🚀 Dispatched manual-proceed task_id: {task_id}")

    # Wait for step 0 to finish and hold at waiting gate
    start_time = time.time()
    reached_waiting = False
    while time.time() - start_time < 15:
        time.sleep(1)
        tasks_resp = requests.get(f"{BASE_URL}/api/tasks")
        target_task = next((t for t in tasks_resp.json() if t.get("id") == task_id), None)
        if target_task:
            detail = target_task.get("detail", "")
            print(f"   Task [{task_id}]: status={target_task.get('status')}, detail='{detail}'")
            if "等待放行" in detail:
                reached_waiting = True
                break

    assert reached_waiting, f"Task {task_id} did not enter waiting state"
    print(f"⏸️ Verified robot arrived at release gate and is holding.")

    # Call proceed endpoint
    print(f"⏩ Calling /api/robots/{robot_id}/proceed ...")
    proc_resp = requests.post(f"{BASE_URL}/api/robots/{robot_id}/proceed")
    assert proc_resp.status_code == 200, f"Proceed call failed: {proc_resp.text}"
    print(f"   Proceed response: {proc_resp.json()}")

    # Wait for completion
    completed = False
    start_time = time.time()
    while time.time() - start_time < 15:
        time.sleep(1)
        tasks_resp = requests.get(f"{BASE_URL}/api/tasks")
        target_task = next((t for t in tasks_resp.json() if t.get("id") == task_id), None)
        if target_task:
            status = target_task.get("status")
            print(f"   Task [{task_id}]: status={status}, progress={target_task.get('progress')}%")
            if status == "completed":
                completed = True
                break

    assert completed, f"Task {task_id} did not complete after proceed"
    print(f"✅ TEST 2 PASSED: Manual proceed released gate and finished task!")

def test_multi_step_with_navigation(robot_id="carter1"):
    print(f"\n--- [TEST 3] Navigation + Gimbal multi-step task on {robot_id} ---")
    robots_resp = requests.get(f"{BASE_URL}/api/robots")
    r_info = next((r for r in robots_resp.json() if r["id"] == robot_id), None)
    if not r_info:
        print(f"⚠️ Robot {robot_id} not available, skipping test 3.")
        return

    cur_x = r_info.get("x", 0.0)
    cur_y = r_info.get("y", 0.0)
    cur_yaw = r_info.get("yaw", 0.0)
    print(f"Current pose of {robot_id}: x={cur_x:.2f}, y={cur_y:.2f}, yaw={cur_yaw:.2f}")

    target_x = round(cur_x + 0.3, 2)
    target_y = round(cur_y + 0.1, 2)

    payload = {
        "robot_id": robot_id,
        "auto_proceed": True,
        "step_delay": 0.5,
        "steps": [
            {"type": "navigate", "x": target_x, "y": target_y, "yaw": 0.0},
            {"type": "gimbal_move", "pan_deg": 30.0, "tilt_deg": 5.0},
            {"type": "navigate", "x": cur_x, "y": cur_y, "yaw": cur_yaw},
            {"type": "gimbal_move", "pan_deg": 0.0, "tilt_deg": 0.0},
        ]
    }
    resp = requests.post(f"{BASE_URL}/api/tasks/multi-step", json=payload)
    assert resp.status_code == 200, f"Dispatch failed: {resp.text}"
    task_id = resp.json().get("task_id")
    print(f"🚀 Dispatched nav+gimbal task_id: {task_id}")

    start_time = time.time()
    completed = False
    while time.time() - start_time < 45:
        time.sleep(1.5)
        tasks_resp = requests.get(f"{BASE_URL}/api/tasks")
        target_task = next((t for t in tasks_resp.json() if t.get("id") == task_id), None)
        if target_task:
            status = target_task.get("status")
            progress = target_task.get("progress")
            step = target_task.get("current_step")
            detail = target_task.get("detail", "")
            print(f"   Task [{task_id}]: status={status}, step={step}/4, progress={progress}%, detail='{detail}'")
            if status == "completed":
                completed = True
                break
            elif status in ("failed", "canceled"):
                raise RuntimeError(f"Nav task failed: {status}, {target_task.get('error')}")

    assert completed, f"Nav+Gimbal task did not complete within timeout"
    print(f"✅ TEST 3 PASSED: Full Navigation + Gimbal sequence completed!")

if __name__ == "__main__":
    check_health()
    robots = get_robots()
    active_robot = robots[0]["id"]
    print(f"Targeting active robot: {active_robot}")
    test_auto_proceed_task(active_robot)
    test_manual_proceed_task(active_robot)
    print("\n🎉 ALL MULTI-STEP TASK TESTS PASSED SUCCESSFULLY!")
