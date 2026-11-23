'use strict';
const histories = new Map();
const runs = new Map();
let currentConversation = '', attachment = null;

let composerNavSeq = 0;

function nextChatNav() { return ++composerNavSeq; }
function chatNavCurrent() { return composerNavSeq; }
