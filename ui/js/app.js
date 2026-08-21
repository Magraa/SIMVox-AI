/**
 * Automated AI Calling System - Native pywebview Desktop Application Controller
 * Connects directly to window.pywebview.api (Zero REST/WebSocket server overhead)
 */

document.addEventListener('DOMContentLoaded', () => {
    // State Variables
    let currentTab = 'tab-studio';
    let callState = 'IDLE';
    let activeCallId = null;
    let callTimerInterval = null;
    let callSeconds = 0;
    let audioEnergy = { caller: 0.0, ai: 0.0 };

    // Canvas Visualizer setup
    const canvas = document.getElementById('audio-waveform-canvas');
    const ctx = canvas ? canvas.getContext('2d') : null;

    // Wait for pywebview JS API binding initialization
    window.addEventListener('pywebviewready', () => {
        console.log('[pywebview] JS API Ready!');
        initApp();
    });

    // Fallback init in case pywebview is already initialized
    if (window.pywebview && window.pywebview.api) {
        initApp();
    }

    function initApp() {
        setupTabs();
        setupEventListeners();
        setupDialpad();
        startCanvasAnimation();
        refreshSystemStatus();

        // Real-time status polling every 3 seconds
        setInterval(refreshSystemStatus, 3000);
    }


    // --------------------------------------------------------------------------
    // Event Handler for Direct Python Event Push Callbacks
    // --------------------------------------------------------------------------
    window.onDesktopEvent = function(eventType, payload) {
        console.log(`[Event Received]: ${eventType}`, payload);

        switch (eventType) {
            case 'call_state_change':
                updateCallState(payload.state, payload.message);
                break;

            case 'incoming_call':
                document.getElementById('current-caller-number').textContent = payload.number || 'Incoming Call...';
                document.getElementById('current-call-type').textContent = 'INCOMING PHONE CALL';
                break;

            case 'call_started':
                activeCallId = payload.call_id;
                document.getElementById('current-caller-number').textContent = payload.phone_number;
                document.getElementById('current-call-type').textContent = `${payload.call_type} CALL IN PROGRESS`;
                startCallTimer();
                updateCallState('CONNECTED');
                clearTranscriptFeed();
                break;

            case 'transcript':
                appendTranscriptBubble(payload.speaker, payload.message);
                break;

            case 'audio_energy':
                if (payload.caller !== undefined) audioEnergy.caller = payload.caller;
                if (payload.ai !== undefined) audioEnergy.ai = payload.ai;
                break;

            case 'call_ended':
                stopCallTimer();
                updateCallState('IDLE');
                refreshHistoryTable();
                break;

            case 'monitor_status':
                document.getElementById('toggle-monitor').checked = payload.active;
                break;

            case 'diagnostic_result':
                if (payload.message) {
                    showToast(payload.message, payload.success ? 'success' : 'error');
                }
                break;
        }
    };

    // --------------------------------------------------------------------------
    // Tab Navigation & UI Controls
    // --------------------------------------------------------------------------
    function setupTabs() {
        const navItems = document.querySelectorAll('.nav-item');
        navItems.forEach(item => {
            item.addEventListener('click', () => {
                const targetTab = item.getAttribute('data-tab');
                navItems.forEach(n => n.classList.remove('active'));
                document.querySelectorAll('.tab-page').forEach(page => page.classList.remove('active'));

                item.classList.add('active');
                document.getElementById(targetTab).classList.add('active');
                currentTab = targetTab;

                if (targetTab === 'tab-history') {
                    refreshHistoryTable();
                } else if (targetTab === 'tab-settings') {
                    loadSettings();
                }
            });
        });
    }

    let aiEnabled = true;

    function updateAIModeUI(enabled) {
        aiEnabled = !!enabled;
        const toggleHeader = document.getElementById('toggle-ai-mode-header');
        const labelHeader = document.getElementById('ai-mode-header-label');
        const toggleMaster = document.getElementById('toggle-ai-master');
        const textMaster = document.getElementById('ai-mode-status-text');
        const iconCard = document.getElementById('ai-mode-card-icon');

        if (toggleHeader) toggleHeader.checked = aiEnabled;
        if (labelHeader) labelHeader.textContent = aiEnabled ? '🤖 AI Mode ON' : '🎧 Direct Call (AI OFF)';
        if (toggleMaster) toggleMaster.checked = aiEnabled;
        if (textMaster) {
            textMaster.textContent = aiEnabled ? '🤖 AI Engine ENABLED' : '🎧 Direct PC Headset Call (AI TURNED OFF)';
            textMaster.style.color = aiEnabled ? 'var(--primary-cyan)' : 'var(--accent-amber)';
        }
        if (iconCard) iconCard.textContent = aiEnabled ? '🤖' : '🎧';
    }

    function setupEventListeners() {
        // AI Mode Toggle Handlers (Header & Settings Master Switch)
        const toggleHeader = document.getElementById('toggle-ai-mode-header');
        const toggleMaster = document.getElementById('toggle-ai-master');

        const onAIToggleChange = async (e) => {
            const isChecked = e.target.checked;
            updateAIModeUI(isChecked);
            if (window.pywebview && window.pywebview.api) {
                try {
                    await window.pywebview.api.toggle_ai_mode(isChecked);
                    showToast(isChecked ? '🤖 AI Engine Enabled!' : '🎧 Direct PC Headset Call Mode Active (AI Turned OFF)', 'info');
                } catch (err) {
                    console.error("Failed to toggle AI mode:", err);
                }
            }
        };

        if (toggleHeader) toggleHeader.addEventListener('change', onAIToggleChange);
        if (toggleMaster) toggleMaster.addEventListener('change', onAIToggleChange);

        // Auto Monitor Toggle
        const toggleMonitor = document.getElementById('toggle-monitor');
        toggleMonitor.addEventListener('change', async () => {
            if (window.pywebview && window.pywebview.api) {
                await window.pywebview.api.toggle_monitor_mode(toggleMonitor.checked);
            }
        });

        // Hang Up Button
        document.getElementById('btn-hangup').addEventListener('click', async () => {
            if (window.pywebview && window.pywebview.api) {
                await window.pywebview.api.end_call();
            }
        });

        // Dial Outbound Button
        document.getElementById('btn-dial-now').addEventListener('click', async () => {
            const numInput = document.getElementById('dialer-phone-input');
            const num = numInput.value.trim();
            const contextInput = document.getElementById('dialer-context-input');
            const context = contextInput ? contextInput.value.trim() : '';

            if (num && window.pywebview && window.pywebview.api) {
                const dialBtn = document.getElementById('btn-dial-now');
                dialBtn.disabled = true;
                dialBtn.textContent = '📞 Dialing...';

                try {
                    const res = await window.pywebview.api.start_outbound_call(num, context);
                    if (!res.success) {
                        showToast(`Could not place call: ${res.error}`, 'error');
                    } else {
                        showToast('Outbound call initialized!', 'success');
                    }
                } catch (err) {
                    console.error("Dial error:", err);
                    showToast('Failed to place outbound call.', 'error');
                } finally {
                    dialBtn.disabled = false;
                    dialBtn.textContent = '📞 Call Now';
                    refreshSystemStatus();
                }
            }
        });

        // Launch Scrcpy Audio Stream Button
        const btnScrcpy = document.getElementById('btn-launch-scrcpy');
        if (btnScrcpy) {
            btnScrcpy.addEventListener('click', async () => {
                if (!window.pywebview || !window.pywebview.api) return;
                btnScrcpy.disabled = true;
                btnScrcpy.textContent = '⚡ Launching...';

                try {
                    const res = await window.pywebview.api.launch_scrcpy_audio_bridge();
                    if (res.success) {
                        showToast('✓ Scrcpy Audio Bridge launched successfully!', 'success');
                    } else {
                        showToast(`❌ Could not launch Scrcpy: ${res.error}`, 'error');
                    }
                } catch (err) {
                    console.error("Failed to launch Scrcpy:", err);
                    showToast('Error launching Scrcpy audio stream.', 'error');
                } finally {
                    btnScrcpy.disabled = false;
                    btnScrcpy.textContent = '⚡ Launch Scrcpy Audio';
                }
            });
        }

        // ADB Modal Triggers
        document.getElementById('btn-open-adb-modal').addEventListener('click', () => {
            document.getElementById('modal-adb').classList.add('active');
        });

        document.getElementById('btn-close-adb-modal').addEventListener('click', () => {
            document.getElementById('modal-adb').classList.remove('active');
        });

        // ADB Connect / Pair Actions
        document.getElementById('btn-connect-adb').addEventListener('click', async () => {
            const ip = document.getElementById('adb-ip').value.trim();
            const port = document.getElementById('adb-port').value.trim() || '5555';
            if (ip && window.pywebview && window.pywebview.api) {
                const res = await window.pywebview.api.connect_wireless_adb(ip, port);
                if (res.success) {
                    alert('Successfully connected to Wireless ADB!');
                    document.getElementById('modal-adb').classList.remove('active');
                    refreshSystemStatus();
                } else {
                    alert('Wireless connection failed.');
                }
            }
        });

        document.getElementById('btn-pair-adb').addEventListener('click', async () => {
            const ip = document.getElementById('adb-ip').value.trim();
            const port = document.getElementById('adb-port').value.trim();
            const code = document.getElementById('adb-code').value.trim();
            if (ip && port && code && window.pywebview && window.pywebview.api) {
                const res = await window.pywebview.api.pair_wireless_adb(ip, port, code);
                if (res.success) {
                    alert('Successfully paired with Wireless ADB!');
                } else {
                    alert('Pairing failed.');
                }
            }
        });

        // ADB Refresh & Selection Actions
        const btnScan = document.getElementById('btn-refresh-adb-devices');
        if (btnScan) {
            btnScan.addEventListener('click', () => refreshSystemStatus());
        }

        const devSelect = document.getElementById('adb-device-select');
        if (devSelect) {
            devSelect.addEventListener('change', async () => {
                const serial = devSelect.value;
                if (window.pywebview && window.pywebview.api) {
                    await window.pywebview.api.select_adb_device(serial);
                    refreshSystemStatus();
                }
            });
        }

        const radioUsb = document.getElementById('radio-mode-usb');
        const radioWireless = document.getElementById('radio-mode-wireless');
        if (radioUsb && radioWireless) {
            radioUsb.addEventListener('change', async () => {
                if (radioUsb.checked && window.pywebview && window.pywebview.api) {
                    await window.pywebview.api.set_adb_mode('usb');
                    refreshSystemStatus();
                }
            });
            radioWireless.addEventListener('change', async () => {
                if (radioWireless.checked && window.pywebview && window.pywebview.api) {
                    await window.pywebview.api.set_adb_mode('wireless');
                    refreshSystemStatus();
                }
            });
        }

        // USB Connect button — scans devices and selects first physical phone
        const btnUsbConnect = document.getElementById('btn-usb-connect');
        if (btnUsbConnect) {
            btnUsbConnect.addEventListener('click', async () => {
                if (!window.pywebview || !window.pywebview.api) return;

                btnUsbConnect.disabled = true;
                btnUsbConnect.textContent = '🔌 Connecting...';

                try {
                    // Set to USB mode, clear any prior target, then rescan
                    await window.pywebview.api.set_adb_mode('usb');
                    await window.pywebview.api.select_adb_device(''); // clear target, re-autodetect
                    const status = await window.pywebview.api.get_system_status();

                    if (status.adb_status === 'CONNECTED') {
                        showToast('✓ Android phone connected via USB!', 'success');
                    } else if (status.adb_status === 'UNAUTHORIZED') {
                        showToast('⚠️ Phone found! Check your phone screen and tap "Allow USB Debugging".', 'warning');
                    } else {
                        showToast('❌ No phone detected. Check USB cable and ensure USB Debugging is enabled.', 'error');
                    }
                    refreshSystemStatus();
                } finally {
                    btnUsbConnect.disabled = false;
                    btnUsbConnect.textContent = '🔌 Connect via USB';
                }
            });
        }

        // Restart ADB Server button — kills and restarts the ADB daemon
        const btnRestartAdb = document.getElementById('btn-restart-adb');
        if (btnRestartAdb) {
            btnRestartAdb.addEventListener('click', async () => {
                if (!window.pywebview || !window.pywebview.api) return;

                btnRestartAdb.disabled = true;
                btnRestartAdb.textContent = '♻️ Restarting...';

                try {
                    await window.pywebview.api.restart_adb_server();
                    showToast('ADB Server restarted. Rescanning devices...', 'info');
                    setTimeout(() => refreshSystemStatus(), 2000);
                } finally {
                    btnRestartAdb.disabled = false;
                    btnRestartAdb.textContent = '♻️ Restart ADB Server';
                }
            });
        }



        document.getElementById('btn-intervene-modal').addEventListener('click', () => {
            document.getElementById('modal-intervene').classList.add('active');
        });

        document.getElementById('btn-close-intervene-modal').addEventListener('click', () => {
            document.getElementById('modal-intervene').classList.remove('active');
        });

        document.getElementById('btn-send-intervene').addEventListener('click', async () => {
            const input = document.getElementById('intervene-text-input');
            const text = input.value.trim();
            if (text && window.pywebview && window.pywebview.api) {
                await window.pywebview.api.inject_ai_response(text);
                input.value = '';
                document.getElementById('modal-intervene').classList.remove('active');
            }
        });

        // Settings Buttons
        const btnAutoAudio = document.getElementById('btn-auto-audio');
        if (btnAutoAudio) {
            btnAutoAudio.addEventListener('click', async () => {
                if (!window.pywebview || !window.pywebview.api) return;

                btnAutoAudio.disabled = true;
                btnAutoAudio.textContent = '🔌 Auto-Configuring...';

                try {
                    const res = await window.pywebview.api.auto_configure_audio_routing();
                    if (res.success) {
                        showToast(res.message, 'success');
                        // Reload settings to select the newly configured virtual cables in the UI dropdowns
                        await loadSettings();
                    } else {
                        showToast(res.error || 'Failed to auto-configure routing.', 'error');
                    }
                } catch (err) {
                    console.error("Auto-configure failed:", err);
                    showToast('Failed to auto-configure audio routing.', 'error');
                } finally {
                    btnAutoAudio.disabled = false;
                    btnAutoAudio.textContent = '🔌 Auto-Configure VB-Cable';
                }
            });
        }

        const btnLoopback = document.getElementById('btn-test-loopback');
        if (btnLoopback) {
            btnLoopback.addEventListener('click', async () => {
                if (!window.pywebview || !window.pywebview.api) return;
                
                btnLoopback.disabled = true;
                btnLoopback.textContent = '🎙️ Testing Loopback...';
                showToast('Playing test tone to Speaker and capturing Microphone...', 'info');

                try {
                    const res = await window.pywebview.api.test_audio_loopback();
                    if (res.success) {
                        showToast(res.message, 'success');
                    } else {
                        showToast(res.message || 'Audio loopback test failed.', 'error');
                    }
                } catch (err) {
                    console.error("Loopback test failed:", err);
                    showToast('Failed to execute loopback diagnostic test.', 'error');
                } finally {
                    btnLoopback.disabled = false;
                    btnLoopback.textContent = '🎙️ Test Audio Loopback';
                }
            });
        }

        document.getElementById('btn-test-voice').addEventListener('click', async () => {
            const voice = document.getElementById('tts-voice-select').value;
            if (window.pywebview && window.pywebview.api) {
                await window.pywebview.api.test_tts_voice("Hello! This is a test of your selected AI neural voice.", voice);
            }
        });

        // Telephony Link Speaker Diagnostics
        const btnStartSpk = document.getElementById('btn-start-spk-test');
        const btnStopSpk = document.getElementById('btn-stop-spk-test');
        if (btnStartSpk && btnStopSpk) {
            btnStartSpk.addEventListener('click', async () => {
                if (!window.pywebview || !window.pywebview.api) return;
                btnStartSpk.disabled = true;
                btnStartSpk.textContent = '▶ Active...';
                btnStopSpk.disabled = false;
                showToast('Playing repeating speech loop to output device. Listen to your phone call!', 'info');
                await window.pywebview.api.start_telephony_speaker_test();
            });

            btnStopSpk.addEventListener('click', async () => {
                if (!window.pywebview || !window.pywebview.api) return;
                btnStopSpk.disabled = true;
                await window.pywebview.api.stop_telephony_speaker_test();
                btnStartSpk.disabled = false;
                btnStartSpk.textContent = '▶ Start Speaker Test';
                showToast('Speaker test loop stopped.', 'success');
            });
        }

        // Telephony Link Microphone Capture Diagnostics
        const btnRunMic = document.getElementById('btn-run-mic-test');
        const progressContainer = document.getElementById('mic-test-progress-container');
        const progressBar = document.getElementById('mic-test-progress-bar');
        const statusText = document.getElementById('mic-test-status-text');
        const percentText = document.getElementById('mic-test-percent');

        if (btnRunMic) {
            btnRunMic.addEventListener('click', async () => {
                if (!window.pywebview || !window.pywebview.api) return;

                btnRunMic.disabled = true;
                btnRunMic.textContent = '🎙️ Recording (3.5s)...';
                progressContainer.style.display = 'block';
                progressBar.style.width = '0%';
                progressBar.style.background = 'var(--accent-emerald)';
                statusText.textContent = 'Recording caller channel... Speak or tap phone mic!';
                percentText.textContent = '0%';

                let progressInterval = setInterval(() => {
                    let curWidth = parseFloat(progressBar.style.width) || 0;
                    if (curWidth < 90) {
                        progressBar.style.width = (curWidth + 10) + '%';
                        percentText.textContent = Math.round(curWidth + 10) + '%';
                    }
                }, 350);

                try {
                    const res = await window.pywebview.api.run_telephony_mic_test();
                    clearInterval(progressInterval);
                    
                    progressBar.style.width = res.volume_percent + '%';
                    percentText.textContent = res.volume_percent + '%';

                    if (res.success) {
                        statusText.textContent = res.message;
                        progressBar.style.background = '#00f5a0'; // green
                        showToast(res.message, 'success');
                    } else {
                        statusText.textContent = res.message;
                        progressBar.style.background = '#ff4b4b'; // red
                        showToast(res.message, 'error');
                    }
                } catch (err) {
                    clearInterval(progressInterval);
                    console.error("Mic diagnostic test failed:", err);
                    showToast('Failed to run microphone diagnostics.', 'error');
                    statusText.textContent = 'Error executing capture test.';
                    progressBar.style.background = '#ff4b4b';
                } finally {
                    btnRunMic.disabled = false;
                    btnRunMic.textContent = '🎙️ Start Mic Capture Test';
                }
            });
        }

        document.getElementById('btn-save-settings').addEventListener('click', async () => {
            const persona = document.getElementById('persona-prompt-input').value;
            const voice = document.getElementById('tts-voice-select').value;
            const inputDev = document.getElementById('audio-input-select').value;
            const outputDev = document.getElementById('audio-output-select').value;
            const directMic = document.getElementById('direct-mic-select') ? document.getElementById('direct-mic-select').value : 'default';
            const directSpeaker = document.getElementById('direct-speaker-select') ? document.getElementById('direct-speaker-select').value : 'default';
            
            if (window.pywebview && window.pywebview.api) {
                const saveBtn = document.getElementById('btn-save-settings');
                saveBtn.disabled = true;
                saveBtn.textContent = '💾 Saving...';

                try {
                    const res = await window.pywebview.api.update_ai_settings(
                        persona, voice, inputDev, outputDev, aiEnabled, directMic, directSpeaker
                    );
                    if (res.success) {
                        showToast('✓ Settings & Audio Routing updated successfully!', 'success');
                    } else {
                        showToast('❌ Failed to update settings.', 'error');
                    }
                } catch (err) {
                    console.error("Failed to save settings:", err);
                    showToast('Error saving settings.', 'error');
                } finally {
                    saveBtn.disabled = false;
                    saveBtn.textContent = '💾 Save Settings';
                }
            }
        });

        // History Detail Modal Close
        document.getElementById('btn-close-detail-modal').addEventListener('click', () => {
            document.getElementById('modal-history-detail').classList.remove('active');
            const player = document.getElementById('call-audio-player');
            if (player) player.pause();
        });

        document.getElementById('btn-refresh-history').addEventListener('click', () => {
            refreshHistoryTable();
        });
    }

    function setupDialpad() {
        const keys = document.querySelectorAll('.dial-key');
        const phoneInput = document.getElementById('dialer-phone-input');
        keys.forEach(k => {
            k.addEventListener('click', () => {
                phoneInput.value += k.textContent.trim();
            });
        });

        // Global keyboard hook for dialing
        window.addEventListener('keydown', (e) => {
            // Do not intercept if user is typing in active text inputs/areas
            const active = document.activeElement;
            if (active && active !== phoneInput && (active.tagName === 'INPUT' || active.tagName === 'TEXTAREA')) {
                return;
            }

            const key = e.key;
            if (/^[0-9]$/.test(key) || key === '*' || key === '#') {
                e.preventDefault();
                phoneInput.value += key;
                phoneInput.focus();
            } else if (key === 'Backspace') {
                e.preventDefault();
                phoneInput.value = phoneInput.value.slice(0, -1);
                phoneInput.focus();
            } else if (key === 'Enter') {
                const dialBtn = document.getElementById('btn-dial-now');
                if (dialBtn && !dialBtn.disabled) {
                    e.preventDefault();
                    dialBtn.click();
                }
            }
        });
    }

    // --------------------------------------------------------------------------
    // State Updates & UI Rendering
    // --------------------------------------------------------------------------
    async function refreshSystemStatus() {
        if (!window.pywebview || !window.pywebview.api) return;
        try {
            const status = await window.pywebview.api.get_system_status();
            const pulse = document.getElementById('adb-pulse');
            const text = document.getElementById('adb-status-text');
            const banner = document.getElementById('adb-status-banner');
            const bannerText = document.getElementById('adb-status-banner-text');

            pulse.className = 'status-pulse';
            if (banner) banner.className = 'adb-status-banner';

            const dialBtn = document.getElementById('btn-dial-now');

            if (status.adb_status === 'CONNECTED') {
                pulse.classList.add('online');
                text.textContent = `ADB Online [${status.adb_mode}]`;
                if (banner) {
                    banner.classList.add('connected');
                    bannerText.textContent = `✓ Connected to target phone [${status.target_id}]`;
                }
                if (dialBtn) {
                    dialBtn.removeAttribute('disabled');
                    dialBtn.title = "Call the entered phone number";
                }
            } else if (status.adb_status === 'UNAUTHORIZED') {
                pulse.classList.add('unauthorized');
                text.textContent = 'ADB Unauthorized';
                if (banner) {
                    banner.classList.add('unauthorized');
                    bannerText.textContent = `⚠️ Phone detected! Please tap 'Allow USB Debugging' on your phone screen.`;
                }
                if (dialBtn) {
                    dialBtn.setAttribute('disabled', 'true');
                    dialBtn.title = "Please authorize USB Debugging on your phone to call";
                }
            } else {
                text.textContent = 'ADB Disconnected';
                if (banner) {
                    banner.classList.add('disconnected');
                    bannerText.textContent = `❌ No Phone Connected. Check USB cable or Wireless ADB.`;
                }
                if (dialBtn) {
                    dialBtn.setAttribute('disabled', 'true');
                    dialBtn.title = "Please connect an Android phone first to place calls";
                }
            }

            // Populate detected devices dropdown
            const devSelect = document.getElementById('adb-device-select');
            if (devSelect && status.devices) {
                const currentVal = devSelect.value;
                devSelect.innerHTML = '<option value="">-- Auto-Detect Physical Phone --</option>';
                status.devices.forEach(dev => {
                    const opt = document.createElement('option');
                    opt.value = dev.serial;
                    const labelType = dev.is_emulator ? '[Emulator]' : '[Phone]';
                    opt.textContent = `${labelType} ${dev.model} (${dev.serial}) - ${dev.state.toUpperCase()}`;
                    if (dev.is_target || dev.serial === currentVal) {
                        opt.selected = true;
                    }
                    devSelect.appendChild(opt);
                });
            }

            document.getElementById('toggle-monitor').checked = status.monitor_active;
            updateAIModeUI(status.ai_enabled !== false);
            updateCallState(status.call_state);
        } catch (e) {
            console.error('Failed to refresh status:', e);
        }
    }


    function updateCallState(state, message = '') {
        callState = state;
        const badge = document.getElementById('call-state-badge');
        const badgeText = document.getElementById('call-state-text');
        const avatar = document.getElementById('avatar-ring');
        const hangupBtn = document.getElementById('btn-hangup');

        badge.className = 'call-state-badge';

        if (state === 'CONNECTED' || state === 'IN_CALL') {
            badge.classList.add('connected');
            badgeText.textContent = aiEnabled ? 'CALL CONNECTED' : 'DIRECT CALL (AI OFF)';
            avatar.classList.add('connected');
            hangupBtn.removeAttribute('disabled');
        } else if (state === 'RINGING') {
            badge.classList.add('ringing');
            badgeText.textContent = 'PHONE RINGING';
            avatar.classList.remove('connected');
            hangupBtn.removeAttribute('disabled');
        } else {
            badge.classList.add('idle');
            badgeText.textContent = 'SYSTEM IDLE';
            avatar.classList.remove('connected');
            hangupBtn.setAttribute('disabled', 'true');
            document.getElementById('current-caller-number').textContent = 'No Active Call';
            document.getElementById('current-call-type').textContent = aiEnabled ? 'Ready for incoming/outgoing phone calls' : 'Ready for Direct Headset Calls (AI Turned OFF)';
            
            // Stop call timer and reset to 00:00 when idle/disconnected
            stopCallTimer();
            const timerEl = document.getElementById('call-timer');
            if (timerEl) {
                timerEl.textContent = '00:00';
            }
        }
    }

    function startCallTimer() {
        stopCallTimer();
        callSeconds = 0;
        const timerEl = document.getElementById('call-timer');
        callTimerInterval = setInterval(() => {
            callSeconds++;
            const mins = String(Math.floor(callSeconds / 60)).padStart(2, '0');
            const secs = String(callSeconds % 60).padStart(2, '0');
            timerEl.textContent = `${mins}:${secs}`;
        }, 1000);
    }

    function stopCallTimer() {
        if (callTimerInterval) {
            clearInterval(callTimerInterval);
            callTimerInterval = null;
        }
    }

    function clearTranscriptFeed() {
        const feed = document.getElementById('transcript-feed');
        feed.innerHTML = '';
        document.getElementById('line-counter').textContent = '0 lines';
    }

    function appendTranscriptBubble(speaker, message) {
        const feed = document.getElementById('transcript-feed');
        const emptyState = feed.querySelector('.feed-empty-state');
        if (emptyState) emptyState.remove();

        const bubble = document.createElement('div');
        bubble.className = `chat-bubble ${speaker.toLowerCase()}`;
        bubble.innerHTML = `
            <div class="bubble-speaker">${speaker === 'AI' ? '🤖 AI Assistant' : '👤 Remote Caller'}</div>
            <div class="bubble-text">${escapeHtml(message)}</div>
        `;

        feed.appendChild(bubble);
        feed.scrollTop = feed.scrollHeight;

        const count = feed.querySelectorAll('.chat-bubble').length;
        document.getElementById('line-counter').textContent = `${count} lines`;
    }

    async function refreshHistoryTable() {
        if (!window.pywebview || !window.pywebview.api) return;
        const records = await window.pywebview.api.get_call_history(50);
        const tbody = document.getElementById('history-table-body');
        tbody.innerHTML = '';

        if (!records || records.length === 0) {
            tbody.innerHTML = '<tr><td colspan="7" class="text-center">No call records found.</td></tr>';
            return;
        }

        records.forEach(rec => {
            const tr = document.createElement('tr');
            const dateStr = rec.start_time ? rec.start_time.split('T')[0] : 'N/A';
            const durStr = rec.duration_seconds ? `${rec.duration_seconds}s` : '0s';

            tr.innerHTML = `
                <td>#${rec.id}</td>
                <td><strong>${escapeHtml(rec.phone_number)}</strong></td>
                <td><span class="badge">${rec.call_type}</span></td>
                <td>${dateStr}</td>
                <td>${durStr}</td>
                <td>${escapeHtml(rec.intent || 'General')}</td>
                <td>
                    <button class="btn btn-glass btn-sm view-detail-btn" data-id="${rec.id}">👁️ View Transcript</button>
                </td>
            `;
            tbody.appendChild(tr);
        });

        tbody.querySelectorAll('.view-detail-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                const id = btn.getAttribute('data-id');
                const rec = records.find(r => r.id == id);
                showHistoryDetailModal(id, rec);
            });
        });
    }

    async function showHistoryDetailModal(callId, record) {
        document.getElementById('detail-modal-title').textContent = `Call Session Record #${callId} (${record.phone_number})`;
        document.getElementById('detail-summary-text').textContent = record.summary || 'No AI summary generated for this call.';

        // Audio Player Setup
        const player = document.getElementById('call-audio-player');
        const container = document.getElementById('audio-player-container');
        if (record.audio_path) {
            container.style.display = 'block';
            player.src = `file:///${record.audio_path.replace(/\\/g, '/')}`;
        } else {
            container.style.display = 'none';
        }

        // Load transcripts
        const transcripts = await window.pywebview.api.get_call_transcripts(parseInt(callId));
        const feed = document.getElementById('detail-transcript-feed');
        feed.innerHTML = '';

        if (transcripts && transcripts.length > 0) {
            transcripts.forEach(t => {
                const bubble = document.createElement('div');
                bubble.className = `chat-bubble ${t.speaker.toLowerCase()}`;
                bubble.innerHTML = `
                    <div class="bubble-speaker">${t.speaker === 'AI' ? '🤖 AI Assistant' : '👤 Remote Caller'}</div>
                    <div class="bubble-text">${escapeHtml(t.message)}</div>
                `;
                feed.appendChild(bubble);
            });
        } else {
            feed.innerHTML = '<p class="text-muted">No transcript lines logged for this call.</p>';
        }

        document.getElementById('modal-history-detail').classList.add('active');
    }

    async function loadSettings() {
        if (!window.pywebview || !window.pywebview.api) return;
        const status = await window.pywebview.api.get_system_status();
        document.getElementById('persona-prompt-input').value = status.ai_persona || '';
        updateAIModeUI(status.ai_enabled !== false);
        
        // 1. Populate voices dropdown
        const voiceSelect = document.getElementById('tts-voice-select');
        if (voiceSelect) {
            try {
                const voices = await window.pywebview.api.get_voice_catalogue();
                voiceSelect.innerHTML = '';
                voices.forEach(voice => {
                    const opt = document.createElement('option');
                    opt.value = voice.id;
                    opt.textContent = voice.label;
                    if (voice.id === status.tts_voice) {
                        opt.selected = true;
                    }
                    voiceSelect.appendChild(opt);
                });
            } catch (err) {
                console.error("Failed to load voice catalogue:", err);
            }
        }

        // 2. Populate audio devices dropdowns
        const inputSelect = document.getElementById('audio-input-select');
        const outputSelect = document.getElementById('audio-output-select');
        const directMicSelect = document.getElementById('direct-mic-select');
        const directSpeakerSelect = document.getElementById('direct-speaker-select');

        if (inputSelect && outputSelect) {
            try {
                const devices = await window.pywebview.api.list_audio_devices();
                
                inputSelect.innerHTML = '<option value="default">-- Default System Input --</option>';
                outputSelect.innerHTML = '<option value="default">-- Default System Output --</option>';
                if (directMicSelect) directMicSelect.innerHTML = '<option value="default">-- Default PC Microphone / Headset Mic --</option>';
                if (directSpeakerSelect) directSpeakerSelect.innerHTML = '<option value="default">-- Default PC Speakers / Headphones --</option>';
                
                devices.forEach(dev => {
                    const apiLabel = dev.host_api ? `[${dev.host_api}] ` : "";
                    if (dev.max_input_channels > 0) {
                        const opt = document.createElement('option');
                        opt.value = dev.name;
                        opt.textContent = `${apiLabel}${dev.name} (Index ${dev.index})`;
                        if (dev.name === status.audio_input_device || String(dev.index) === String(status.audio_input_device)) {
                            opt.selected = true;
                        }
                        inputSelect.appendChild(opt);

                        if (directMicSelect) {
                            const opt2 = document.createElement('option');
                            opt2.value = dev.name;
                            opt2.textContent = `${apiLabel}${dev.name} (Index ${dev.index})`;
                            if (dev.name === status.direct_mic_device || String(dev.index) === String(status.direct_mic_device)) {
                                opt2.selected = true;
                            }
                            directMicSelect.appendChild(opt2);
                        }
                    }
                    if (dev.max_output_channels > 0) {
                        const opt = document.createElement('option');
                        opt.value = dev.name;
                        opt.textContent = `${apiLabel}${dev.name} (Index ${dev.index})`;
                        if (dev.name === status.audio_output_device || String(dev.index) === String(status.audio_output_device)) {
                            opt.selected = true;
                        }
                        outputSelect.appendChild(opt);

                        if (directSpeakerSelect) {
                            const opt2 = document.createElement('option');
                            opt2.value = dev.name;
                            opt2.textContent = `${apiLabel}${dev.name} (Index ${dev.index})`;
                            if (dev.name === status.direct_speaker_device || String(dev.index) === String(status.direct_speaker_device)) {
                                opt2.selected = true;
                            }
                            directSpeakerSelect.appendChild(opt2);
                        }
                    }
                });
            } catch (err) {
                console.error("Failed to load audio devices list:", err);
            }
        }
    }

    // --------------------------------------------------------------------------
    // Real-Time Canvas Audio Visualizer Animation
    // --------------------------------------------------------------------------
    function startCanvasAnimation() {
        if (!ctx) return;
        let phase = 0;

        function render() {
            requestAnimationFrame(render);
            phase += 0.05;

            ctx.clearRect(0, 0, canvas.width, canvas.height);

            // Draw Caller Waveform (Cyan)
            ctx.beginPath();
            ctx.strokeStyle = '#00f2fe';
            ctx.lineWidth = 2;
            const callerAmp = (callState === 'CONNECTED' || callState === 'IN_CALL') ? (audioEnergy.caller > 0 ? 30 : 5) : 2;

            for (let x = 0; x < canvas.width; x++) {
                const y = canvas.height / 2 + Math.sin(x * 0.03 + phase) * callerAmp * Math.sin(x * 0.01);
                if (x === 0) ctx.moveTo(x, y);
                else ctx.lineTo(x, y);
            }
            ctx.stroke();

            // Draw AI Waveform (Purple)
            ctx.beginPath();
            ctx.strokeStyle = '#7f00ff';
            ctx.lineWidth = 2;
            const aiAmp = (callState === 'CONNECTED' || callState === 'IN_CALL') ? (audioEnergy.ai > 0 ? 35 : 5) : 2;

            for (let x = 0; x < canvas.width; x++) {
                const y = canvas.height / 2 + Math.cos(x * 0.03 + phase * 1.2) * aiAmp * Math.sin(x * 0.01);
                if (x === 0) ctx.moveTo(x, y);
                else ctx.lineTo(x, y);
            }
            ctx.stroke();
        }

        render();
    }

    function escapeHtml(str) {
        return (str || '').replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    }

    // ------------------------------------------------------------------
    // Toast notification helper (non-blocking status messages)
    // ------------------------------------------------------------------
    function showToast(message, type = 'info') {
        // Remove any existing toast
        const existing = document.getElementById('app-toast');
        if (existing) existing.remove();

        const toast = document.createElement('div');
        toast.id = 'app-toast';
        toast.className = `toast toast-${type}`;
        toast.textContent = message;
        document.body.appendChild(toast);

        // Animate in
        requestAnimationFrame(() => toast.classList.add('visible'));

        // Auto-dismiss after 4 seconds
        setTimeout(() => {
            toast.classList.remove('visible');
            setTimeout(() => toast.remove(), 400);
        }, 4000);
    }
});

