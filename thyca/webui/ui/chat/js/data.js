// Chat data layer — shared backend calls live in ../share/data.js.
// API: GET /api/bots, GET /api/conversations,
//      GET /api/bots/<id>/dm, GET /api/groups/<group_id>,
//      GET /api/bots/<id>/dm/operations.
'use strict';
const histories = new Map();
const runs = new Map();          // typed key dm:<bot>/group:<id> -> in-flight turn token
let currentConversation = '', attachment = null;

let composerNavSeq = 0;          // view generation shared by selectBot/loadConversation

function nextChatNav() { return ++composerNavSeq; }
function chatNavCurrent() { return composerNavSeq; }
