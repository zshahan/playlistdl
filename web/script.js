console.log("Script loaded"); // Add this line at the beginning of script.js

// Job backing the in-flight download, if any - lets the Cancel button
// reach the right job on the server-side queue.
let currentJobId = null;

async function download() {
    const spotifyLink = document.getElementById('spotifyLink').value;

    if (!spotifyLink) {
        document.getElementById('result').innerText = "Please enter a Spotify link.";
        return;
    }

    // Clear previous logs and result
    const logsElement = document.getElementById('logs');
    logsElement.innerHTML = "";
    document.getElementById('result').innerText = "";

    // Show and reset the progress bar
    const progressBar = document.getElementById('progress');
    progressBar.style.display = 'block';
    progressBar.value = 0;
    const increment = 10; // Smaller increment for more gradual progress

    currentJobId = null;
    const cancelButton = document.getElementById('cancelButton');
    cancelButton.style.display = 'inline-block';
    cancelButton.disabled = false;

    // Create an EventSource to listen to the server-sent events
    const eventSource = new EventSource(`/download?spotify_link=${encodeURIComponent(spotifyLink)}`);

    // Sent once, right when the job is created, so Cancel has something to
    // target even before any log lines arrive (e.g. while still queued).
    eventSource.addEventListener('job', function(event) {
        currentJobId = event.data;
    });

    function stopWatching() {
        eventSource.close();
        progressBar.style.display = 'none';
        cancelButton.style.display = 'none';
        currentJobId = null;
        if (document.getElementById('adminControls').style.display !== 'none') {
            refreshJobQueue();
        }
    }

    eventSource.onmessage = function(event) {
        const log = event.data;

        if (log.startsWith("DOWNLOAD:")) {
            // Download link received, set progress to 100%
            progressBar.value = 100;

            const path = log.split("DOWNLOAD: ")[1].trim();  // ✅ define path BEFORE using it
            console.log("Download path from server:", path); // ✅ now it's safe to use

            const downloadLink = document.createElement('a');
            downloadLink.href = `/downloads/${path}`;
            downloadLink.download = decodeURIComponent(path.split('/').pop());

            downloadLink.innerText = "Click to download your file";
            document.getElementById('result').appendChild(downloadLink);

            // Close the EventSource and hide the progress bar
            stopWatching();
        } else if (log.includes("Download completed") || log.includes("Download process completed successfully")) {
            // Admin downloads finish here instead of a DOWNLOAD: message, so
            // the bar needs to be completed and closed out the same way.
            logsElement.innerHTML += "Download completed successfully.<br>";
            progressBar.value = 100;

            // Close the EventSource now - otherwise the browser treats the
            // server ending the stream as a dropped connection and silently
            // reconnects, kicking off a duplicate download.
            stopWatching();
        } else if (log.startsWith("Error") || log.includes("Job killed by user request") || log.includes("Job removed from queue")) {
            // Display error/cancellation message and close EventSource
            document.getElementById('result').innerText = log.startsWith("Error") ? `Error: ${log}` : log;
            stopWatching();
        } else {
            // Increase progress gradually
            progressBar.value = Math.min(progressBar.value + increment, 95);

            // Append log output to logs section
            logsElement.innerHTML += log + "<br>";
            logsElement.scrollTop = logsElement.scrollHeight;
        }
    };

    eventSource.onerror = function() {
        // Only show error if no success message was received
        if (!logsElement.innerHTML.includes("Download completed successfully")) {
            document.getElementById('result').innerText = "Error occurred while downloading.";
        }
        stopWatching();
    };
}

// Cancels the download currently being watched: removes it if it's still
// waiting in the queue, or kills its process if it has already started.
async function cancelDownload() {
    if (!currentJobId) return;
    const cancelButton = document.getElementById('cancelButton');
    cancelButton.disabled = true;
    try {
        let response = await fetch(`/jobs/${currentJobId}`, { method: 'DELETE' });
        let data = await response.json();
        if (!data.success) {
            response = await fetch(`/jobs/${currentJobId}/kill`, { method: 'POST' });
            data = await response.json();
        }
        if (!data.success) {
            console.error("Could not cancel job:", data.message);
        }
    } catch (e) {
        console.error("Cancel failed:", e);
    } finally {
        cancelButton.disabled = false;
    }
}
// Function to handle the Admin / Log Out button behavior
function handleAdminButton() {
    if (document.getElementById('adminButton').innerText === "Admin") {
        showLoginModal();  // Show login modal if not logged in
    } else {
        logout();  // Log out if already logged in
    }
}

// Show login modal
function showLoginModal() {
    const loginModal = document.getElementById('loginModal');
    loginModal.classList.add('show');  // Show modal on button click
}

// Hide login modal
function closeLoginModal() {
    const loginModal = document.getElementById('loginModal');
    loginModal.classList.remove('show');  // Hide modal when closed
}

// Check login status, toggle button text, and show/hide admin message

async function loadDownloadOptions() {
    try {
        const response = await fetch('/download-options');
        const data = await response.json();
        if (data.success) {
            const select = document.getElementById('downloadPathSelect');
            const input = document.getElementById('downloadPath');
            const button = document.getElementById('setPathButton');
            
            // Clear existing options
            select.innerHTML = '';
            
            const options = data.options || [];
            const currentPath = data.current_path;
            
            if (options.length > 0) {
                // Populate dropdown
                options.forEach(opt => {
                    const el = document.createElement('option');
                    el.value = opt.path;
                    el.innerText = opt.label;
                    select.appendChild(el);
                });
                
                // Add Custom option
                const customEl = document.createElement('option');
                customEl.value = 'custom';
                customEl.innerText = 'Custom Path...';
                select.appendChild(customEl);
                
                select.style.display = 'inline-block';
                
                // Check if currentPath matches one of the option paths
                const matchingOpt = options.find(opt => opt.path === currentPath);
                if (matchingOpt) {
                    select.value = currentPath;
                    input.style.display = 'none';
                    button.style.display = 'none';
                } else {
                    select.value = 'custom';
                    input.value = currentPath || '';
                    input.style.display = 'inline-block';
                    button.style.display = 'inline-block';
                }
            } else {
                // No options configured, fallback to plain input
                select.style.display = 'none';
                input.value = currentPath || '';
                input.style.display = 'inline-block';
                button.style.display = 'inline-block';
            }
        }
    } catch (e) {
        console.error("Error loading download options:", e);
    }
}

async function handlePathSelectChange() {
    const select = document.getElementById('downloadPathSelect');
    const input = document.getElementById('downloadPath');
    const button = document.getElementById('setPathButton');
    const messageDiv = document.getElementById('pathMessage');
    
    const val = select.value;
    if (val === 'custom') {
        input.style.display = 'inline-block';
        button.style.display = 'inline-block';
        messageDiv.innerText = '';
    } else {
        input.style.display = 'none';
        button.style.display = 'none';
        input.value = val;
        
        messageDiv.innerText = "Saving path...";
        messageDiv.style.color = "yellow";
        
        try {
            const response = await fetch('/set-download-path', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({path: val})
            });
            const data = await response.json();
            if (data.success) {
                messageDiv.innerText = `Download path set successfully to: ${data.new_path}`;
                messageDiv.style.color = "lime";
            } else {
                messageDiv.innerText = `Error: ${data.message}`;
                messageDiv.style.color = "red";
            }
        } catch (e) {
            messageDiv.innerText = `Error saving path: ${e}`;
            messageDiv.style.color = "red";
        }
    }
}

async function checkLoginStatus() {
    const response = await fetch('/check-login');
    const data = await response.json();
    const adminButton = document.getElementById('adminButton');
    const adminMessage = document.getElementById('adminMessage');
    const adminControls = document.getElementById('adminControls');

    if (data.loggedIn) {
        adminButton.innerText = "Log Out";
        adminMessage.style.display = "block";
        adminControls.style.display = "block";
        await loadDownloadOptions();
        startJobQueuePolling();
    } else {
        adminButton.innerText = "Admin";
        adminMessage.style.display = "none";
        adminControls.style.display = "none";
        const select = document.getElementById('downloadPathSelect');
        const input = document.getElementById('downloadPath');
        const button = document.getElementById('setPathButton');
        if (select) select.style.display = 'none';
        if (input) input.style.display = 'inline-block';
        if (button) button.style.display = 'inline-block';
        stopJobQueuePolling();
    }
}

// --- Admin job queue panel -------------------------------------------

let jobQueuePollHandle = null;

function startJobQueuePolling() {
    if (jobQueuePollHandle) return;
    refreshJobQueue();
    jobQueuePollHandle = setInterval(refreshJobQueue, 4000);
}

function stopJobQueuePolling() {
    if (jobQueuePollHandle) {
        clearInterval(jobQueuePollHandle);
        jobQueuePollHandle = null;
    }
}

function formatJobTime(epochSeconds) {
    if (!epochSeconds) return '-';
    return new Date(epochSeconds * 1000).toLocaleTimeString();
}

async function refreshJobQueue() {
    const tbody = document.querySelector('#jobQueueTable tbody');
    if (!tbody) return;

    try {
        const response = await fetch('/jobs');
        const data = await response.json();
        if (!data.success) return;

        tbody.innerHTML = '';
        data.jobs.forEach(job => {
            const row = document.createElement('tr');

            const linkCell = document.createElement('td');
            linkCell.textContent = job.link && job.link.length > 45 ? job.link.slice(0, 45) + '…' : job.link;
            linkCell.title = job.link || '';
            row.appendChild(linkCell);

            const statusCell = document.createElement('td');
            statusCell.textContent = job.status;
            statusCell.className = `job-status job-status-${job.status}`;
            row.appendChild(statusCell);

            const startedCell = document.createElement('td');
            startedCell.textContent = formatJobTime(job.started_at);
            row.appendChild(startedCell);

            const actionsCell = document.createElement('td');
            if (job.status === 'queued') {
                const removeBtn = document.createElement('button');
                removeBtn.textContent = 'Remove';
                removeBtn.onclick = () => removeQueuedJob(job.id);
                actionsCell.appendChild(removeBtn);
            } else if (job.status === 'running') {
                const killBtn = document.createElement('button');
                killBtn.textContent = 'Kill';
                killBtn.onclick = () => killRunningJob(job.id);
                actionsCell.appendChild(killBtn);
            }
            row.appendChild(actionsCell);

            tbody.appendChild(row);
        });
    } catch (e) {
        console.error("Error loading job queue:", e);
    }
}

async function removeQueuedJob(jobId) {
    try {
        await fetch(`/jobs/${jobId}`, { method: 'DELETE' });
    } catch (e) {
        console.error("Error removing job:", e);
    }
    refreshJobQueue();
}

async function killRunningJob(jobId) {
    try {
        await fetch(`/jobs/${jobId}/kill`, { method: 'POST' });
    } catch (e) {
        console.error("Error killing job:", e);
    }
    refreshJobQueue();
}


async function logout() {
    const response = await fetch('/logout', { method: 'POST' });
    const data = await response.json();

    if (data.success) {
        await checkLoginStatus();  // ✅ Add this line
    }
}

// After successful login, change button text to "Log Out" and show the message

async function login() {
    const username = document.getElementById('username').value;
    const password = document.getElementById('password').value;

    const response = await fetch('/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password })
    });

    const data = await response.json();
    if (data.success) {
        document.getElementById('loginMessage').innerText = "Login successful!";
        closeLoginModal();
        await checkLoginStatus();  // ✅ Add this line
    } else {
        document.getElementById('loginMessage').innerText = "Login failed. Try again.";
    }
}


async function setDownloadPath() {
    const path = document.getElementById('downloadPath').value;
    const messageDiv = document.getElementById('pathMessage');

    if (!path) {
        messageDiv.innerText = "Path cannot be empty.";
        return;
    }

    const response = await fetch('/set-download-path', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({path})
    });

    const data = await response.json();

    if (data.success) {
        messageDiv.innerText = `Download path set successfully to: ${data.new_path}`;
        messageDiv.style.color = "lime";
        await loadDownloadOptions();
    } else {
        messageDiv.innerText = `Error: ${data.message}`;
        messageDiv.style.color = "red";
    }
}

// Call checkLoginStatus on page load to set initial button state and message visibility
window.onload = checkLoginStatus;

