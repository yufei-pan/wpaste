let lastKnownUpdate = 0;

async function checkForUpdates() {
	const response = await fetch('/last-update');
	const data = await response.json();
	if (data.last_update > lastKnownUpdate) {
		lastKnownUpdate = data.last_update;
		fetchMessages(); // Fetch messages only if there's an update
	}
}

setInterval(checkForUpdates, 5000); // Check every 5 seconds

document.getElementById('messageForm').addEventListener('submit', async function(e) {
	e.preventDefault();
	const message = document.getElementById('message').value;
	const formData = new FormData(this);
	formData.append('message', message);

	const xhr = new XMLHttpRequest();
	xhr.open('POST', '/message', true);

	xhr.upload.addEventListener('progress', function(e) {
		if (e.lengthComputable) {
			const percentComplete = (e.loaded / e.total) * 100;
			const uploadProgress = document.getElementById('uploadProgress');
			uploadProgress.textContent = `Upload progress: ${percentComplete.toFixed(2)}%`;
			uploadProgress.className = 'uploading';
			document.getElementById('progressBar').style.width = `${percentComplete}%`;
		}
	});

	xhr.onload = function() {
		const uploadProgress = document.getElementById('uploadProgress');
		if (xhr.status === 200) {
			const result = JSON.parse(xhr.responseText);
			document.getElementById('messageForm').reset();
			document.getElementById('message').value = '';
			document.getElementById('image-name').textContent = '';
			document.getElementById('video-name').textContent = '';
			document.getElementById('file-name').textContent = '';
			uploadProgress.textContent = 'Upload complete';
			uploadProgress.className = 'upload-complete';
			document.getElementById('progressBar').style.width = '0%';
			checkForUpdates();
		} else {
			uploadProgress.textContent = 'Upload failed';
			uploadProgress.className = 'upload-failed';
			document.getElementById('progressBar').style.width = '0%';
		}
	};

	xhr.send(formData);
});

document.getElementById('clearButton').addEventListener('click', function() {
	document.getElementById('messageForm').reset(); // Reset the form
	// Clear any filenames displayed
	document.getElementById('image-name').textContent = '';
	document.getElementById('video-name').textContent = '';
	document.getElementById('file-name').textContent = '';
});

function isHTML(str) {
	// Parse the string as HTML
	const doc = new DOMParser().parseFromString(str, "text/html");

	// Check for parsererrors
	if (doc.querySelector('parsererror')) {
		return false;
	}

	// Check if any element nodes exist in the head or body
	const hasElementNodes = (node) => node.nodeType === 1 && node.tagName.toLowerCase() !== 'html';
	const bodyHasNodes = Array.from(doc.body.childNodes).some(hasElementNodes);
	const headHasNodes = Array.from(doc.head.childNodes).some(hasElementNodes);

	return bodyHasNodes || headHasNodes;
}

async function fetchMessages() {
    const response = await fetch('/messages');
    const result = await response.json();
    const messagesDiv = document.getElementById('messages');
    messagesDiv.innerHTML = '';

    result.messages.forEach((message) => {
        const messageElement = document.createElement('div');
        messageElement.classList.add('message');
        messageElement.id = `message-${message.id}`;

        let contentToCopy = null;       // Will store the text or image element for "copy"
        let contentElementRef = null;   // Reference so we can toggle raw vs rendered

        // Create a container for the message content
        const contentContainer = document.createElement('div');
        contentContainer.classList.add('content-container');

        if (message.type === 'text') {
            const isMessageHTML = isHTML(message.content);

            const rawPre = document.createElement('pre');
            rawPre.textContent = message.content; // The raw content

            if (isMessageHTML) {
                // If HTML, create a sanitized container
                const sanitizedDiv = document.createElement('div');
                const sanitizedHTML = DOMPurify.sanitize(message.content);
                sanitizedDiv.innerHTML = sanitizedHTML;
                contentElementRef = sanitizedDiv;  // We'll show the sanitized version by default
                contentContainer.appendChild(sanitizedDiv);
            } else {
                // If it's not HTML, just show it in a <pre>
                contentElementRef = rawPre;
                contentContainer.appendChild(rawPre);
            }
            
            // For the copyToClipboard function, we'll use whichever element is currently displayed
            contentToCopy = contentElementRef;

        } else if (message.type === 'image') {
            if (message.filename && message.filename !== 'image.png') {
                const imgName = document.createElement('p');
                imgName.textContent = message.filename;
                contentContainer.appendChild(imgName);
            }
            const img = document.createElement('img');
            img.src = message.content;
            img.style.maxWidth = '100%';
            contentContainer.appendChild(img);
            contentElementRef = img;
            contentToCopy = img;

        } else if (message.type === 'video') {
            if (message.filename) {
                const videoName = document.createElement('p');
                videoName.textContent = message.filename;
                contentContainer.appendChild(videoName);
            }
            const video = document.createElement('video');
            video.src = message.content;
            video.controls = true;
            video.style.maxWidth = '100%';
            contentContainer.appendChild(video);
            contentElementRef = video;
            contentToCopy = video;

        } else if (message.type === 'file') {
            const a = document.createElement('a');
            a.href = message.content;
            a.textContent = message.filename || 'Download File';
            a.download = '';
            contentContainer.appendChild(a);
            contentElementRef = a;
            contentToCopy = a;

        } else {
            console.error('Unknown message type:', message.type);
            const pre = document.createElement('pre');
            pre.textContent = 'Unknown message type';
            contentContainer.appendChild(pre);
            contentElementRef = pre;
            contentToCopy = pre;
        }

        messageElement.appendChild(contentContainer);

        // Show time info
        const dateTime = document.createElement('p');
        const date = new Date(message.timestamp * 1000);
        dateTime.textContent = `Time: ${date.toDateString()} ${date.toTimeString()}`;
        messageElement.appendChild(dateTime);

        // ---- CREATE A BUTTONS CONTAINER SO WE CAN LINE THEM UP ----
        const buttonsContainer = document.createElement('div');
        buttonsContainer.classList.add('buttons-container'); 
        // You can style this class in CSS (e.g., display: inline-flex; gap: 8px; etc.)

        // Copy button
        const copyButton = document.createElement('button');
        copyButton.textContent = 'Copy to Clipboard';
        copyButton.classList.add('copy-button');
        copyButton.onclick = function() { copyToClipboard(contentToCopy); };
        buttonsContainer.appendChild(copyButton);

        // Delete button
        const deleteButton = document.createElement('button');
        deleteButton.textContent = 'Delete';
        deleteButton.classList.add('delete-button');
        deleteButton.onclick = function() { deleteMessage(message.id); };
        buttonsContainer.appendChild(deleteButton);

        // (Optional) If it's text AND recognized as HTML, add a "Show Raw" button
        if (message.type === 'text' && isHTML(message.content)) {
            // Create the "Show Raw" button
            const showRawButton = document.createElement('button');
            showRawButton.textContent = 'Show Raw';
            showRawButton.classList.add('show-raw-button');
            messageElement.setAttribute('data-show-raw', 'false'); 
            // false => currently showing sanitized HTML

            showRawButton.onclick = function() {
                const isCurrentlyRaw = (messageElement.getAttribute('data-show-raw') === 'true');
                
                if (isCurrentlyRaw) {
                    // Switch to sanitized HTML
                    const sanitizedDiv = document.createElement('div');
                    const sanitizedHTML = DOMPurify.sanitize(message.content);
                    sanitizedDiv.innerHTML = sanitizedHTML;
                    contentContainer.replaceChild(sanitizedDiv, contentElementRef);
                    contentElementRef = sanitizedDiv;
                    messageElement.setAttribute('data-show-raw', 'false');
                    showRawButton.textContent = 'Show Raw';
                } else {
                    // Switch to raw <pre>
                    const preElement = document.createElement('pre');
                    preElement.textContent = message.content;
                    contentContainer.replaceChild(preElement, contentElementRef);
                    contentElementRef = preElement;
                    messageElement.setAttribute('data-show-raw', 'true');
                    showRawButton.textContent = 'Show Rendered';
                }
            };

            // Add the showRawButton to the same container
            buttonsContainer.appendChild(showRawButton);
        }

        // Finally, append the buttons container to the message element
        messageElement.appendChild(buttonsContainer);

        // Append the entire message to the messages div
        messagesDiv.appendChild(messageElement);
    });
}


function copyToClipboard(element) {
	if (!element) {
		showToast('No content to copy!');
		return;
	}
	if (element.tagName === 'IMG') {
		if (typeof ClipboardItem !== "undefined") {
			fetch(element.src)
				.then(res => res.blob())
				.then(blob => {
					if (blob.type === "image/png") {
						// If the image is already a PNG, use it directly
						const item = new ClipboardItem({ "image/png": blob });
						navigator.clipboard.write([item]).then(() => {
							showToast('Image copied to clipboard!');
						}, (err) => {
							showToast('Failed to copy image: ' + err);
						});
					} else {
						// Convert the image to PNG if it's not already PNG
						const canvas = document.createElement('canvas');
						const ctx = canvas.getContext('2d');
						const img = new Image();
						img.onload = () => {
							canvas.width = img.width;
							canvas.height = img.height;
							ctx.drawImage(img, 0, 0);
							canvas.toBlob((pngBlob) => {
								const item = new ClipboardItem({ "image/png": pngBlob });
								navigator.clipboard.write([item]).then(() => {
									showToast('Converted Image copied to clipboard!');
								}, (err) => {
									showToast('Failed to copy image: ' + err);
								});
							}, 'image/png');
						};
						img.src = URL.createObjectURL(blob);
					}
				});
		} else {
			// Fallback: Copy the image URL to the clipboard.
			navigator.clipboard.writeText(element.src).then(() => {
				showToast('Image URL copied to clipboard!');
			}, (err) => {
				showToast('Failed to copy image URL: ' + err);
			});
		}
	} else if (element.tagName === 'PRE' || element.tagName === 'P') {
		navigator.clipboard.writeText(element.textContent).then(() => {
			showToast('Text copied to clipboard!');
		}, (err) => {
			showToast('Failed to copy text: ' + err);
		});
	} else if (element.tagName === 'DIV') {
		const selection = window.getSelection();
		const range = document.createRange();
		range.selectNodeContents(element);
		selection.removeAllRanges();
		selection.addRange(range);
		document.execCommand('copy');
		selection.removeAllRanges();
		showToast('HTML copied to clipboard!');
	} else if (element.tagName === 'A') {
		navigator.clipboard.writeText(element.href).then(() => {
			showToast('Link copied to clipboard!');
		}, (err) => {
			showToast('Failed to copy link: ' + err);
		});
	} else if (element.tagName === 'VIDEO') {
		// copy the video URL to the clipboard
		navigator.clipboard.writeText(element.src).then(() => {
			showToast('Video URL copied to clipboard!');
		}, (err) => {
			showToast('Failed to copy video URL: ' + err);
		});
	} else {
		showToast('Unsupported content!');
	}
}

document.addEventListener('copy', function(e) {
	if (document.activeElement.id === 'message') {
		// Let the browser handle copying if the message box is focused.
		return;
	}
	const selection = window.getSelection();
	if (!selection.toString().trim()) {
		e.preventDefault(); // Prevent the default copy behavior
		// Attempt to copy the newest message
		const newestMessage = document.querySelector('.message');
		if (newestMessage) {
			const contentElement = newestMessage.querySelector('pre') 
				|| newestMessage.querySelector('img') 
				|| newestMessage.querySelector('div');
			if (contentElement) {
				copyToClipboard(contentElement);
			}
		}
	}
});

function showToast(message) {
	const toast = document.createElement('div');
	toast.textContent = message;
	toast.className = 'toast';
	document.body.appendChild(toast);
	setTimeout(() => {
		document.body.removeChild(toast);
	}, 3000); // The toast message disappears after 3 seconds.
}

document.body.addEventListener('paste', async function(e) {
	if (document.activeElement.id === 'message') {
		// If the message box is focused, let the browser handle pasting.
		return;
	}
	e.preventDefault();
	const items = e.clipboardData.items;
	for (const item of items) {
		console.log(item.kind, item.type);
		if (item.kind === 'string') {
			// Provide a callback function to getAsString
			item.getAsString((text) => {
				const formData = new FormData();
				formData.append('message', text);
				fetch('/message', {
					method: 'POST',
					body: formData,
				})
				.then(response => response.json())
				.then(result => {
					checkForUpdates();
				})
				.catch(error => console.error('Error:', error));
			});
		} else if (item.kind === 'file' && item.type.startsWith('image/')) {
			const file = item.getAsFile();
			const formData = new FormData();
			formData.append('image', file);
			formData.append('message', '');
			fetch('/message', {
				method: 'POST',
				body: formData,
			})
			.then(response => response.json())
			.then(result => {
				checkForUpdates();
			})
			.catch(error => console.error('Error:', error));
		}
	}
});

document.body.addEventListener('dragover', function(e) {
	e.preventDefault();
	e.stopPropagation();
	// Optional: Add some visual feedback
	e.target.style.background = '#f0f0f0';
});

document.body.addEventListener('dragenter', function(e) {
	e.preventDefault();
	e.stopPropagation();
	// Optional: More visual feedback on drag enter
	e.target.style.background = '#e0e0e0';
});

document.body.addEventListener('dragleave', function(e) {
	e.preventDefault();
	e.stopPropagation();
	// Optional: Revert visual feedback
	e.target.style.background = '';
});

document.body.addEventListener('drop', function(e) {
	e.preventDefault();
	e.stopPropagation();
	e.target.style.background = ''; // Revert visual feedback
	const files = e.dataTransfer.files;
	const formData = new FormData();
	for (const file of files) {
		if (file.type.startsWith('image/')) {
			formData.append('image', file);
		}
	}
	formData.append('message', '');
	fetch('/message', {
		method: 'POST',
		body: formData,
	}).then(response => response.json())
	.then(result => {
		checkForUpdates(); // Update the message list
	});
});

async function deleteMessage(messageIndex) {
	// The backend will need to identify messages by an index or ID
	await fetch(`/delete/${messageIndex}`, { method: 'POST' });
	checkForUpdates();
}

async function deleteAllMessages() {
	await fetch('/delete_all', { method: 'POST' });
	checkForUpdates();
}

document.getElementById('image').addEventListener('change', function() {
	const fileNames = Array.from(this.files).map(file => file.name).join('; ');
	document.getElementById('image-name').textContent = fileNames;
});

document.getElementById('video').addEventListener('change', function() {
	const fileNames = Array.from(this.files).map(file => file.name).join('; ');
	document.getElementById('video-name').textContent = fileNames;
});

document.getElementById('file').addEventListener('change', function() {
	const fileNames = Array.from(this.files).map(file => file.name).join('; ');
	document.getElementById('file-name').textContent = fileNames;
});

window.onload = function() {
	checkForUpdates();
};
