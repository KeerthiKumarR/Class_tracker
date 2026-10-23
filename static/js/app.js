/**
 * Class Tracker · Web Management Portal & Onboarding JavaScript
 * Multi-subject support, AI syllabus extraction, live Google Sheet inspection.
 */

// Application State
const state = {
    currentStep: 1,
    activeTab: 'onboarding',
    sheetId: '',
    sheetTitle: '',
    availableTabs: [],
    selectedTab: '',
    extractedUnits: [],
    multiCoursesDetected: [],
    selectedFile: null,
    savedCourse: null,
    coursesList: [],
};

// DOM Content Loaded Initializer
document.addEventListener('DOMContentLoaded', () => {
    initNavigation();
    initWizardStepper();
    initSheetVerification();
    initSyllabusIngestion();
    initCourseSave();
    initDashboard();
    initCopyButtons();
    initAudioSandbox();
    initEmailSettings();
    checkServerHealth();
});

// ==============================================================================
// 1. Navigation & Tab Switching
// ==============================================================================
function initNavigation() {
    const tabs = document.querySelectorAll('.nav-tab');
    tabs.forEach(tab => {
        tab.addEventListener('click', () => {
            const target = tab.dataset.tab;
            switchToTab(target);
        });
    });
}

function switchToTab(tabId) {
    state.activeTab = tabId;
    document.querySelectorAll('.nav-tab').forEach(t => {
        t.classList.toggle('active', t.dataset.tab === tabId);
    });
    document.querySelectorAll('.tab-panel').forEach(p => {
        p.classList.toggle('active', p.id === `panel-${tabId}`);
    });

    if (tabId === 'dashboard') {
        loadDashboardData();
    } else if (tabId === 'email-settings') {
        loadEmailSettings();
    }
}

// ==============================================================================
// 2. Wizard Stepper Transitions
// ==============================================================================
function initWizardStepper() {
    // Step 1 -> Step 2
    document.getElementById('btn-step1-next').addEventListener('click', () => {
        const name = document.getElementById('course-name').value.trim();
        const code = document.getElementById('course-code').value.trim();
        if (!name || !code) {
            showToast('Please enter both course title and subject code.', 'error');
            return;
        }
        goToStep(2);
    });

    // Step 2 Back & Next
    document.getElementById('btn-step2-back').addEventListener('click', () => goToStep(1));
    document.getElementById('btn-step2-next').addEventListener('click', () => {
        if (!state.extractedUnits || state.extractedUnits.length === 0) {
            showToast('Please extract syllabus units before proceeding.', 'error');
            return;
        }
        updateStep3Summary();
        goToStep(3);
    });

    // Step 3 Back
    document.getElementById('btn-step3-back').addEventListener('click', () => goToStep(2));

    // Step 4 View on Dashboard
    document.getElementById('btn-view-dashboard').addEventListener('click', () => {
        switchToTab('dashboard');
    });
}

function goToStep(stepNum) {
    state.currentStep = stepNum;

    // Update Step Cards
    document.querySelectorAll('.wizard-step-card').forEach((card, idx) => {
        card.classList.toggle('active', idx + 1 === stepNum);
    });

    // Update Stepper Nodes & Dividers
    for (let i = 1; i <= 4; i++) {
        const node = document.getElementById(`step-node-${i}`);
        if (node) {
            node.classList.toggle('active', i === stepNum);
            node.classList.toggle('completed', i < stepNum);
        }
        const div = document.getElementById(`step-div-${i}`);
        if (div) {
            div.classList.toggle('active', i < stepNum);
        }
    }

    window.scrollTo({ top: 100, behavior: 'smooth' });
}

function updateStep3Summary() {
    const name = document.getElementById('course-name').value.trim();
    const code = document.getElementById('course-code').value.trim();
    const tab = document.getElementById('sheet-tab-select').value;

    document.getElementById('sc-course-name').textContent = name;
    document.getElementById('sc-course-code').textContent = code;
    document.getElementById('sc-sheet-tab').textContent = tab;
    document.getElementById('sc-units-count').textContent = `${state.extractedUnits.length} Units`;
    document.getElementById('sc-filter-code').textContent = code;
    document.getElementById('sc-filter-name').textContent = name;
}

// ==============================================================================
// 3. Google Sheet Inspection & Verification
// ==============================================================================
function initSheetVerification() {
    const btn = document.getElementById('btn-verify-sheet');
    const input = document.getElementById('sheet-url');

    btn.addEventListener('click', async () => {
        const url = input.value.trim();
        if (!url) {
            showToast('Please provide a Google Sheet URL or ID.', 'error');
            return;
        }

        const spinner = document.getElementById('sheet-verify-spinner');
        spinner.classList.remove('hidden');
        btn.disabled = true;

        try {
            const res = await fetch('/api/verify-sheet', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ sheet_url: url }),
            });
            const data = await res.json();

            if (!res.ok) {
                throw new Error(data.error || 'Failed to inspect Google Sheet');
            }

            state.sheetId = data.sheet_id;
            state.sheetTitle = data.title;
            state.availableTabs = data.tabs || [];

            // Update UI
            document.getElementById('v-sheet-title').textContent = `Connected: ${data.title}`;
            const tabSelect = document.getElementById('sheet-tab-select');
            tabSelect.innerHTML = '';

            data.tabs.forEach(t => {
                const opt = document.createElement('option');
                opt.value = t.title;
                opt.textContent = `${t.title} (${t.row_count} rows)`;
                if (t.title === data.suggested_tab) {
                    opt.selected = true;
                    state.selectedTab = t.title;
                }
                tabSelect.appendChild(opt);
            });

            tabSelect.addEventListener('change', (e) => {
                state.selectedTab = e.target.value;
            });

            // Reveal verification box
            const vBox = document.getElementById('sheet-verification-box');
            if (vBox) {
                vBox.classList.remove('hidden');
            }

            showToast(`Connected to spreadsheet: ${data.title}`, 'success');
        } catch (err) {
            showToast(err.message, 'error');
        } finally {
            spinner.classList.add('hidden');
            btn.disabled = false;
        }
    });
}

// ==============================================================================
// 4. Syllabus Document & Text Auto-Ingestion
// ==============================================================================
function initSyllabusIngestion() {
    // Mode switcher (File / Text / Existing)
    const ingestTabs = document.querySelectorAll('.ingest-tab');
    ingestTabs.forEach(t => {
        t.addEventListener('click', () => {
            ingestTabs.forEach(tab => tab.classList.remove('active'));
            t.classList.add('active');

            const mode = t.dataset.ingest;
            document.querySelectorAll('.ingest-panel').forEach(p => {
                p.classList.toggle('active', p.id === `ingest-panel-${mode}`);
            });
        });
    });

    // File Dropzone Handling
    const dropzone = document.getElementById('syllabus-dropzone');
    const fileInput = document.getElementById('syllabus-file-input');
    const chip = document.getElementById('file-chip');
    const chipName = document.getElementById('file-chip-name');
    const chipRemove = document.getElementById('file-chip-remove');

    ['dragenter', 'dragover'].forEach(name => {
        dropzone.addEventListener(name, (e) => {
            e.preventDefault();
            dropzone.classList.add('dragover');
        });
    });

    ['dragleave', 'drop'].forEach(name => {
        dropzone.addEventListener(name, (e) => {
            e.preventDefault();
            dropzone.classList.remove('dragover');
        });
    });

    dropzone.addEventListener('drop', (e) => {
        if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
            handleFileSelect(e.dataTransfer.files[0]);
        }
    });

    fileInput.addEventListener('change', (e) => {
        if (e.target.files && e.target.files.length > 0) {
            handleFileSelect(e.target.files[0]);
        }
    });

    chipRemove.addEventListener('click', (e) => {
        e.stopPropagation();
        state.selectedFile = null;
        fileInput.value = '';
        chip.classList.add('hidden');
    });

    function handleFileSelect(file) {
        state.selectedFile = file;
        chipName.textContent = `${file.name} (${Math.round(file.size / 1024)} KB)`;
        chip.classList.remove('hidden');
    }

    // Run AI Extraction
    document.getElementById('btn-run-extraction').addEventListener('click', async () => {
        const activeTabEl = document.querySelector('.ingest-tab.active');
        const activeMode = activeTabEl ? activeTabEl.dataset.ingest : 'file';
        const courseName = document.getElementById('course-name').value.trim() || 'Course';
        const courseCode = document.getElementById('course-code').value.trim() || 'CRS';
        const spinner = document.getElementById('extract-spinner');
        const btn = document.getElementById('btn-run-extraction');

        const formData = new FormData();
        formData.append('course_name', courseName);
        formData.append('course_code', courseCode);

        // Pass available tabs from spreadsheet to help AI match
        const tabOptions = Array.from(document.getElementById('sheet-tab-select').options).map(o => o.value);
        if (tabOptions.length > 0) {
            formData.append('available_tabs', JSON.stringify(tabOptions));
        }

        if (activeMode === 'file') {
            if (!state.selectedFile) {
                showToast('Please select or drop a syllabus file first.', 'error');
                return;
            }
            formData.append('file', state.selectedFile);
        } else if (activeMode === 'text') {
            const text = document.getElementById('syllabus-raw-text').value.trim();
            if (!text) {
                showToast('Please paste syllabus text into the box.', 'error');
                return;
            }
            formData.append('text', text);
        } else {
            showToast('Please select a file or paste syllabus text.', 'info');
            return;
        }

        spinner.classList.remove('hidden');
        btn.disabled = true;

        try {
            const res = await fetch('/api/parse-syllabus', {
                method: 'POST',
                body: formData,
            });
            const data = await res.json();

            if (!res.ok) {
                throw new Error(data.error || 'Extraction failed');
            }

            if (data.multi_course && data.courses && data.courses.length > 1) {
                showToast(`🎉 Detected ${data.courses_count} distinct courses in document!`, 'success');
                state.multiCoursesDetected = data.courses;
                // Render first course by default
                renderExtractedUnits(data.courses[0].units);
                if (data.courses[0].matched_tab) {
                    const select = document.getElementById('sheet-tab-select');
                    if (Array.from(select.options).some(o => o.value === data.courses[0].matched_tab)) {
                        select.value = data.courses[0].matched_tab;
                    }
                }
            } else if (data.units && data.units.length > 0) {
                renderExtractedUnits(data.units);
                showToast(`✅ Successfully extracted ${data.units.length} syllabus topics!`, 'success');
            } else {
                showToast('No structured units could be extracted from this document.', 'error');
            }
        } catch (err) {
            showToast('AI Parsing Error: ' + err.message, 'error');
        } finally {
            spinner.classList.add('hidden');
            btn.disabled = false;
        }
    });

    // Add Unit Manually
    document.getElementById('btn-add-unit').addEventListener('click', () => {
        const code = document.getElementById('course-code').value.trim() || 'CRS';
        const newIndex = state.extractedUnits.length + 1;
        const newUnit = {
            unit_id: `${code}-${newIndex}`,
            week: Math.ceil(newIndex / 3),
            topic: `New Topic ${newIndex}`,
            content: ['Key concept 1'],
            classwork: ['Class problem 1'],
            homework: ['Homework exercise 1'],
        };
        state.extractedUnits.push(newUnit);
        renderExtractedUnits(state.extractedUnits);
    });
}

function renderExtractedUnits(units) {
    state.extractedUnits = units || [];
    const countEl = document.getElementById('units-count');
    const tbody = document.getElementById('units-table-body');
    const container = document.getElementById('extracted-units-container');

    countEl.textContent = state.extractedUnits.length;
    tbody.innerHTML = '';

    if (state.extractedUnits.length === 0) {
        container.style.display = 'none';
        return;
    }

    container.style.display = 'block';

    state.extractedUnits.forEach((u, idx) => {
        const courseCode = (document.getElementById('course-code').value || 'UNIT').trim().toUpperCase();
        const displayId = (u.unit_id && String(u.unit_id).trim()) ? String(u.unit_id).trim() : `${courseCode}-${idx + 1}`;
        u.unit_id = displayId;
        const tr = document.createElement('tr');
        tr.innerHTML = `
            <td><span class="unit-id-pill">${escapeHtml(displayId)}</span></td>
            <td>Wk ${u.week || 1}</td>
            <td><strong>${escapeHtml(u.topic)}</strong></td>
            <td><small>${escapeHtml((u.content || []).join(', '))}</small></td>
            <td><small>${escapeHtml((u.classwork || []).join(', ')) || '-'}</small></td>
            <td><small>${escapeHtml((u.homework || []).join(', ')) || '-'}</small></td>
            <td>
                <button type="button" class="btn btn-sm btn-secondary" onclick="deleteUnit(${idx})">🗑️</button>
            </td>
        `;
        tbody.appendChild(tr);
    });
}

window.deleteUnit = function(index) {
    state.extractedUnits.splice(index, 1);
    renderExtractedUnits(state.extractedUnits);
};

// ==============================================================================
// 5. Course Save & Pairing Code Generation
// ==============================================================================
function initCourseSave() {
    const btn = document.getElementById('btn-save-course');

    btn.addEventListener('click', async () => {
        const name = document.getElementById('course-name').value.trim();
        const code = document.getElementById('course-code').value.trim();
        const tab = document.getElementById('sheet-tab-select').value;
        const teacherEmail = document.getElementById('teacher-email').value.trim();
        const calendarId = (document.getElementById('calendar-id')?.value || '').trim();
        const spinner = document.getElementById('save-spinner');

        btn.disabled = true;
        spinner.classList.remove('hidden');

        try {
            const res = await fetch('/api/save-course', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    name: name,
                    code: code,
                    sheet_id: state.sheetId,
                    tab_name: tab,
                    teacher_email: teacherEmail,
                    calendar_id: calendarId,
                    units: state.extractedUnits,
                }),
            });
            const data = await res.json();

            if (!res.ok) {
                throw new Error(data.error || 'Failed to save course profile');
            }

            state.savedCourse = data.course;
            const pairingCode = data.pairing_code || `${code}-2026`;

            // Display in Step 4
            document.getElementById('pairing-code-display').textContent = pairingCode;
            document.getElementById('inst-code-strong').textContent = pairingCode;

            showToast(`Course '${code}' configured! Pairing code: ${pairingCode}`, 'success');
            goToStep(4);
        } catch (err) {
            showToast('Save Error: ' + err.message, 'error');
        } finally {
            btn.disabled = false;
            spinner.classList.add('hidden');
        }
    });
}

// ==============================================================================
// 6. Dashboard & Real-Time Sheet Viewer
// ==============================================================================
function initDashboard() {
    document.getElementById('btn-refresh-dashboard').addEventListener('click', () => {
        loadDashboardData();
    });
}

async function loadDashboardData() {
    try {
        const res = await fetch('/api/courses');
        const data = await res.json();
        const courses = data.courses || [];
        state.coursesList = courses;

        document.getElementById('metric-total-courses').textContent = courses.length;
        const totalUnits = courses.reduce((sum, c) => sum + (c.units ? c.units.length : 0), 0);
        document.getElementById('metric-total-units').textContent = totalUnits;

        renderCourseCards(courses);

        if (courses.length > 0) {
            loadCourseSheetProgress(courses[0].id);
        }
    } catch (err) {
        showToast('Error loading courses dashboard: ' + err.message, 'error');
    }
}

function renderCourseCards(courses) {
    const container = document.getElementById('courses-cards-container');
    container.innerHTML = '';

    if (courses.length === 0) {
        container.innerHTML = `<div class="preset-card"><p>No courses configured yet. Click "New Course Setup" to add one!</p></div>`;
        return;
    }

    courses.forEach(c => {
        const card = document.createElement('div');
        card.className = 'course-card';
        card.innerHTML = `
            <div class="cc-header">
                <div class="cc-title-area">
                    <span class="cc-code-pill">${escapeHtml(c.code)}</span>
                    <h3 class="cc-name">${escapeHtml(c.name)}</h3>
                </div>
                <span class="cc-pairing-badge">${escapeHtml(c.pairing_code || 'ACTIVE')}</span>
            </div>

            <div class="progress-container">
                <div class="progress-info">
                    <span>Syllabus Scope</span>
                    <span>${c.units ? c.units.length : 0} Total Units</span>
                </div>
                <div class="progress-track">
                    <div class="progress-fill" style="width: 100%;"></div>
                </div>
            </div>

            <div class="cc-footer-actions">
                <button type="button" class="btn btn-sm btn-primary" onclick="loadCourseSheetProgress('${c.id}')">
                    📋 View Live Sheet Progress
                </button>
                <a href="https://docs.google.com/spreadsheets/d/${c.sheet_id}" target="_blank" class="btn btn-sm btn-secondary">
                    📊 Sheet (Tab: ${escapeHtml(c.tab_name)})
                </a>
                <a href="/api/download-config/${c.id}" class="btn btn-sm btn-secondary" title="Download .env">
                    ⚙️ Config
                </a>
                <button type="button" class="btn btn-sm btn-secondary" onclick="deleteCourseProfile('${c.id}', '${escapeHtml(c.name)}')" title="Delete Course" style="color: #ff6b6b; border-color: rgba(255,107,107,0.3);">
                    🗑️
                </button>
            </div>
        `;
        container.appendChild(card);
    });
}

window.deleteCourseProfile = async function(courseId, courseName) {
    if (!confirm(`Are you sure you want to delete course "${courseName}"?`)) return;
    try {
        const resp = await fetch(`/api/course/${courseId}`, { method: 'DELETE' });
        if (resp.ok) {
            loadActiveCourses();
        } else {
            alert('Failed to delete course');
        }
    } catch (e) {
        alert('Error deleting course: ' + e);
    }
};

window.loadCourseSheetProgress = async function(courseId) {
    const viewer = document.getElementById('live-sheet-viewer-box');
    const tbody = document.getElementById('lsv-table-body');
    const titleEl = document.getElementById('lsv-title');
    const subEl = document.getElementById('lsv-subtitle');
    const sheetLink = document.getElementById('lsv-open-sheet-link');

    tbody.innerHTML = `<tr><td colspan="7" style="text-align: center; padding: 24px;">🔄 Reading live progression from Google Sheet...</td></tr>`;

    try {
        const res = await fetch(`/api/course/${courseId}/progress`);
        const data = await res.json();

        if (!res.ok) {
            throw new Error(data.error || 'Failed to read sheet');
        }

        titleEl.textContent = `Live Spreadsheet Progression: ${data.course_name}`;
        subEl.textContent = `Connected to Google Sheet '${data.sheet_title}' (Tab: ${data.tab_name})`;
        sheetLink.href = `https://docs.google.com/spreadsheets/d/${data.sheet_id || state.sheetId}`;

        // Update KPIs
        document.getElementById('metric-completed-units').textContent = data.completed_count;
        document.getElementById('metric-overall-pct').textContent = `${data.percent_overall}%`;

        tbody.innerHTML = '';
        if (!data.rows || data.rows.length === 0) {
            tbody.innerHTML = `<tr><td colspan="7" style="text-align: center;">No unit rows found on this tab.</td></tr>`;
            return;
        }

        data.rows.forEach(r => {
            const tr = document.createElement('tr');
            const statClass = r.status.toLowerCase().includes('complete') ? 'badge-success' :
                             (r.status.toLowerCase().includes('started') || r.status.toLowerCase().includes('progress') ? 'badge-warning' : 'badge-accent');

            tr.innerHTML = `
                <td><code>Row ${r.row_number}</code></td>
                <td><span class="unit-id-pill">${escapeHtml(r.unit_id)}</span></td>
                <td><strong>${escapeHtml(r.topic)}</strong></td>
                <td>${escapeHtml(r.date_taught || '-')}</td>
                <td><span class="badge ${statClass}">${escapeHtml(r.status)}</span></td>
                <td><strong>${escapeHtml(r.percent_complete || '0%')}</strong></td>
                <td><small>${escapeHtml(r.notes || r.classwork || '-')}</small></td>
            `;
            tbody.appendChild(tr);
        });

    } catch (err) {
        tbody.innerHTML = `<tr><td colspan="7" style="text-align: center; color: var(--accent-rose); padding: 20px;">⚠️ ${escapeHtml(err.message)}</td></tr>`;
    }
};

// ==============================================================================
// 7. Audio Sandbox Test Execution
// ==============================================================================
function initAudioSandbox() {
    const input = document.getElementById('sandbox-audio-input');
    if (!input) return;
    const chip = document.getElementById('audio-chip');
    const chipName = document.getElementById('audio-chip-name');
    const chipRemove = document.getElementById('audio-chip-remove');
    const btn = document.getElementById('btn-run-audio-test');
    let selectedAudio = null;

    input.addEventListener('change', (e) => {
        if (e.target.files && e.target.files.length > 0) {
            selectedAudio = e.target.files[0];
            chipName.textContent = `${selectedAudio.name} (${Math.round(selectedAudio.size / 1024)} KB)`;
            chip.classList.remove('hidden');
        }
    });

    chipRemove.addEventListener('click', (e) => {
        e.stopPropagation();
        selectedAudio = null;
        input.value = '';
        chip.classList.add('hidden');
    });

    btn.addEventListener('click', async () => {
        if (!selectedAudio) {
            showToast('Please select an audio file to test.', 'error');
            return;
        }

        const terminalBox = document.getElementById('terminal-log-box');
        const terminalOut = document.getElementById('terminal-output');
        const statusBadge = document.getElementById('pipeline-status-badge');
        const spinner = document.getElementById('audio-test-spinner');

        terminalBox.classList.remove('hidden');
        terminalOut.textContent = `[UPLOAD] Uploading ${selectedAudio.name} to AI pipeline...\n[GROQ] Initializing Whisper speech-to-text...\n`;
        statusBadge.textContent = 'Processing...';
        btn.disabled = true;
        spinner.classList.remove('hidden');

        const formData = new FormData();
        formData.append('file', selectedAudio);
        formData.append('course_id', document.getElementById('sandbox-course-select').value);

        try {
            const res = await fetch('/upload', {
                method: 'POST',
                body: formData,
            });
            const data = await res.json();

            terminalOut.textContent += JSON.stringify(data, null, 2);

            if (res.ok && data.status === 'success') {
                statusBadge.textContent = 'Completed ✅';
                showToast('Lecture processed and sheet updated successfully!', 'success');
            } else {
                statusBadge.textContent = 'Failed ❌';
                showToast('Processing encountered warnings or errors.', 'error');
            }
        } catch (err) {
            terminalOut.textContent += `\n[ERROR] Network or server failure: ${err.message}`;
            statusBadge.textContent = 'Error';
            showToast(err.message, 'error');
        } finally {
            btn.disabled = false;
            spinner.classList.add('hidden');
        }
    });
}

// ==============================================================================
// 8. Clipboard Helpers
// ==============================================================================
function initCopyButtons() {
    const copyCodeBtn = document.getElementById('btn-copy-code');
    if (copyCodeBtn) {
        copyCodeBtn.addEventListener('click', () => {
            const code = document.getElementById('pairing-code-display').textContent.trim();
            navigator.clipboard.writeText(code);
            showToast(`Copied code '${code}' to clipboard!`, 'info');
        });
    }

    const copyBotEmailBtn = document.getElementById('btn-copy-bot-email');
    if (copyBotEmailBtn) {
        copyBotEmailBtn.addEventListener('click', () => {
            const email = document.getElementById('bot-service-email')?.textContent.trim() || 'class-tracker-bot@class-tracker-507704.iam.gserviceaccount.com';
            navigator.clipboard.writeText(email);
            showToast(`Copied Bot Email to clipboard: ${email}`, 'success');
        });
    }

    const copyCmdBtn = document.getElementById('btn-copy-command');
    if (copyCmdBtn) {
        copyCmdBtn.addEventListener('click', () => {
            const cmd = 'xattr -cr /Applications/ClassTracker.app';
            navigator.clipboard.writeText(cmd);
            showToast('Copied Terminal command: ' + cmd, 'info');
        });
    }
}

// ==============================================================================
// 8b. Email Notifications & Gmail SMTP Settings
// ==============================================================================
function initEmailSettings() {
    const btnSave = document.getElementById('btn-save-email');
    const btnTest = document.getElementById('btn-test-email');

    if (btnSave) {
        btnSave.addEventListener('click', async () => {
            const sender = document.getElementById('smtp-sender-email').value.trim();
            const password = document.getElementById('smtp-app-password').value.trim();

            if (!sender) {
                showToast('Please enter your Gmail address.', 'error');
                return;
            }
            if (!password) {
                showToast('Please enter your 16-character Google App Password.', 'error');
                return;
            }

            btnSave.disabled = true;
            btnSave.textContent = 'Saving...';

            try {
                const res = await fetch('/api/settings/email', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        gmail_sender: sender,
                        gmail_app_password: password,
                        test_send: false
                    })
                });
                const data = await res.json();
                if (res.ok) {
                    showToast('Gmail credentials saved successfully!', 'success');
                    loadEmailSettings();
                } else {
                    showToast(data.error || 'Failed to save email settings.', 'error');
                }
            } catch (err) {
                showToast('Network error while saving email settings: ' + err.message, 'error');
            } finally {
                btnSave.disabled = false;
                btnSave.textContent = '💾 Save Credentials';
            }
        });
    }

    if (btnTest) {
        btnTest.addEventListener('click', async () => {
            const sender = document.getElementById('smtp-sender-email').value.trim();
            const password = document.getElementById('smtp-app-password').value.trim();

            if (!sender) {
                showToast('Please enter your Gmail address first.', 'error');
                return;
            }
            if (!password) {
                showToast('Please enter your App Password to test connection.', 'error');
                return;
            }

            btnTest.disabled = true;
            btnTest.textContent = '🧪 Testing SMTP...';

            try {
                const res = await fetch('/api/settings/email', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        gmail_sender: sender,
                        gmail_app_password: password,
                        test_send: true
                    })
                });
                const data = await res.json();
                if (res.ok) {
                    showToast(data.message || 'Verification email sent! Check your inbox.', 'success');
                    loadEmailSettings();
                } else {
                    showToast(data.error || 'SMTP Test failed. Please check your credentials.', 'error');
                }
            } catch (err) {
                showToast('SMTP Test error: ' + err.message, 'error');
            } finally {
                btnTest.disabled = false;
                btnTest.textContent = '🧪 Send Test Email';
            }
        });
    }

    // Initial load in background
    loadEmailSettings();
}

async function loadEmailSettings() {
    const statusEl = document.getElementById('email-status-text');
    const senderInput = document.getElementById('smtp-sender-email');
    const pwdInput = document.getElementById('smtp-app-password');

    if (!statusEl) return;

    try {
        const res = await fetch('/api/settings/email');
        if (!res.ok) return;
        const data = await res.json();

        if (data.gmail_sender && senderInput && !senderInput.value) {
            senderInput.value = data.gmail_sender;
        }

        if (data.is_configured) {
            if (pwdInput && !pwdInput.value) {
                pwdInput.placeholder = '•••••••••••••••• (Password configured)';
            }
            statusEl.innerHTML = `<span style="color: var(--success, #10b981); font-weight: 600;">✅ Active:</span> Configured for <strong>${escapeHtml(data.gmail_sender)}</strong>`;
        } else {
            statusEl.innerHTML = `<span style="color: var(--warning, #f59e0b); font-weight: 500;">⚠️ Not configured:</span> Summary emails are paused until configured.`;
        }
    } catch (e) {
        statusEl.textContent = 'Could not load configuration status.';
    }
}

// ==============================================================================
// 9. Utilities & Server Health Check
// ==============================================================================
async function checkServerHealth() {
    try {
        const res = await fetch('/healthz');
        const data = await res.json();
        const pill = document.getElementById('server-status');
        if (data.alive) {
            pill.className = 'status-pill online';
            pill.querySelector('.status-text').textContent = 'Server Active';
        }
    } catch (e) {
        const pill = document.getElementById('server-status');
        pill.className = 'status-pill offline';
        pill.querySelector('.status-text').textContent = 'Connecting...';
    }
}

function showToast(message, type = 'info') {
    const container = document.getElementById('toast-container');
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    const icon = type === 'success' ? '✅' : (type === 'error' ? '⚠️' : 'ℹ️');
    toast.innerHTML = `<span>${icon}</span><span>${escapeHtml(message)}</span>`;
    container.appendChild(toast);

    setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transform = 'translateX(50px)';
        setTimeout(() => toast.remove(), 300);
    }, 4500);
}

function escapeHtml(text) {
    if (!text) return '';
    return String(text)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}
