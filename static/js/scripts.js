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
	
	const response = await fetch('/message', {
		method: 'POST',
		body: formData,
	});

	const result = await response.json();
	//alert(result.message);
	document.getElementById('message').value = '';
	checkForUpdates();
});

function isHTML(str) {
	const doc = new DOMParser().parseFromString(str, "text/html");
	return Array.from(doc.body.childNodes).some(node => node.nodeType === 1);
}

async function fetchMessages() {
	const response = await fetch('/messages');
	const result = await response.json();
	const messagesDiv = document.getElementById('messages');
	messagesDiv.innerHTML = '';
	result.messages.forEach((message) => {
		const messageElement = document.createElement('div');
		messageElement.classList.add('message');

		let contentToCopy = ''; // This will store the text or image element.

		if (message.type === 'text') {
			const isMessageHTML = isHTML(message.content);
			if (isMessageHTML) {
				const container = document.createElement('div');
				container.innerHTML = DOMPurify.sanitize(message.content);
				messageElement.appendChild(container);
				contentToCopy = container; // Prepare to copy sanitized HTML as plain text.
			} else {
				const pre = document.createElement('pre');
				pre.textContent = message.content;
				messageElement.appendChild(pre);
				contentToCopy = pre; // Plain text content.
			}
		} else if (message.type === 'image') {
			const img = document.createElement('img');
			img.src = message.content;
			img.style.maxWidth = '100%';
			messageElement.appendChild(img);
			contentToCopy = img; // Image element for copying.
		} else {
			console.error('Unknown message type:', message.type);
			const pre = document.createElement('pre');
			pre.textContent = 'Unknown message type';
			messageElement.appendChild(pre);
			contentToCopy = pre; // Fallback to plain text.
		}

		const dateTime = document.createElement('p');
		const date = new Date(message.timestamp * 1000);
		dateTime.textContent = `Time: ${date.toDateString()} ${date.toTimeString()}`;
		messageElement.appendChild(dateTime);

		const copyButton = document.createElement('button');
		copyButton.textContent = 'Copy to Clipboard';
		copyButton.classList.add('copy-button');
		copyButton.onclick = function() { copyToClipboard(contentToCopy); };
		messageElement.appendChild(copyButton);

		const deleteButton = document.createElement('button');
		deleteButton.textContent = 'Delete';
		deleteButton.classList.add('delete-button');
		deleteButton.onclick = function() { deleteMessage(message.id); };
		messageElement.appendChild(deleteButton);

		messagesDiv.appendChild(messageElement);
	});
}

function copyToClipboard(element) {
	if (element.tagName === 'IMG') {
		if (typeof ClipboardItem !== "undefined") {
			fetch(element.src)
				.then(res => res.blob())
				.then(blob => {
					const item = new ClipboardItem({ "image/png": blob });
					navigator.clipboard.write([item]).then(function() {
						showToast('Image copied to clipboard!');
					}, function(err) {
						showToast('Failed to copy image: ' + err);
					});
				});
		} else {
			// Fallback: Copy the image URL to the clipboard.
			navigator.clipboard.writeText(element.src).then(function() {
				showToast('Image URL copied to clipboard!');
			}, function(err) {
				showToast('Failed to copy image URL: ' + err);
			});
		}
	} else {
		navigator.clipboard.writeText(element.textContent).then(function() {
			showToast('Text copied to clipboard!');
		}, function(err) {
			showToast('Failed to copy text: ' + err);
		});
	}
}

document.addEventListener('copy', function(e) {
	if (document.activeElement.id === 'message') {
		// If the message box is focused, let the browser handle copying.
		return;
	}
	const selection = window.getSelection();
	if (!selection.toString().trim()) {
		// Nothing is selected, attempt to copy the newest message
		const newestMessage = document.querySelector('.message');
		if (newestMessage) {
			const contentElement = newestMessage.querySelector('pre') || newestMessage.querySelector('img') || newestMessage.querySelector('div');
			if (contentElement) {
				if (contentElement.tagName === 'IMG') {
					// If the newest message is an image, use the image copying logic
					if (typeof ClipboardItem !== "undefined" && contentElement.src) {
						e.preventDefault(); // Prevent the default copy behavior
						fetch(contentElement.src)
							.then(res => res.blob())
							.then(blob => {
								const item = new ClipboardItem({ "image/png": blob });
								navigator.clipboard.write([item]);
								showToast('Image copied to clipboard!');
							})
							.catch(err => showToast('Failed to copy image: ' + err));
					} else {
						navigator.clipboard.writeText(contentElement.src);
						showToast('Image URL copied to clipboard!');
					}
				} else if (contentElement.tagName === 'PRE' ) {
					// If the newest message is text, use the text copying logic
					e.preventDefault(); // Prevent the default copy behavior
					navigator.clipboard.writeText(contentElement.textContent)
						.then(() => showToast('Text copied to clipboard!'))
						.catch(err => showToast('Failed to copy text: ' + err));
				} else if (contentElement.tagName === 'DIV') {
					// If the newest message is HTML, use the HTML copying logic
					e.preventDefault(); // Prevent the default copy behavior
					const range = document.createRange();
					range.selectNode(contentElement);
					selection.removeAllRanges();
					selection.addRange(range);
					document.execCommand('copy');
					selection.removeAllRanges();
					showToast('HTML copied to clipboard!');
				}
			}
		}
	}
});



function showToast(message) {
	const toast = document.createElement('div');
	toast.textContent = message;
	toast.style.position = 'fixed';
	toast.style.bottom = '20px';
	toast.style.left = '50%';
	toast.style.transform = 'translateX(-50%)';
	toast.style.backgroundColor = 'rgba(0,0,0,0.7)';
	toast.style.color = 'white';
	toast.style.padding = '10px 20px';
	toast.style.borderRadius = '5px';
	toast.style.zIndex = '1000';
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

window.onload = function() {
	checkForUpdates();
};