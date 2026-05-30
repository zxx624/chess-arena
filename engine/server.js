// xqwlight chess engine HTTP server
// Wraps xqwlight JS engine, exposes POST /analyze endpoint

"use strict";

var fs = require('fs');
var vm = require('vm');
var http = require('http');

// Load xqwlight modules into global scope
function loadScript(path) {
  var code = fs.readFileSync(path, 'utf8');
  // Remove "use strict" to avoid scope issues
  code = code.replace(/"use strict";?\s*/g, '');
  vm.runInThisContext(code, { filename: path });
}

loadScript(__dirname + '/cchess.js');
loadScript(__dirname + '/position.js');
loadScript(__dirname + '/search.js');
loadScript(__dirname + '/book.js');

// === UCCI <-> xqwlight coordinate conversion ===
// xqwlight: 16x16 board, FILE_LEFT=3, RANK_TOP=3
// UCCI: file a-i = col 0-8, rank 0-9 (rank 0 = red home = bottom)

function ucciSqToXqwlight(file, rank) {
  return (12 - rank) * 16 + (3 + file);
}

function xqwlightSqToUcci(sq) {
  var y = sq >> 4;
  var x = sq & 15;
  return { file: x - 3, rank: 12 - y };
}

function moveToUcci(mv) {
  var src = xqwlightSqToUcci(SRC(mv));
  var dst = xqwlightSqToUcci(DST(mv));
  return String.fromCharCode('a'.charCodeAt(0) + src.file) + src.rank +
         String.fromCharCode('a'.charCodeAt(0) + dst.file) + dst.rank;
}

function fenToXqwlight(fen) {
  var parts = fen.split(' ');
  if (parts.length >= 2) {
    parts[1] = (parts[1] === 'r') ? 'w' : 'b';
  }
  return parts.join(' ');
}

// === Main analyze function ===
function analyze(fen, depth) {
  var pos = new Position();
  pos.fromFen(fenToXqwlight(fen));
  
  var search = new Search(pos, 16);
  var mv = search.searchMain(depth || 3, 10000);
  
  if (mv <= 0) {
    return { error: "no move found" };
  }
  
  return {
    best_move: moveToUcci(mv),
    score: 0,
    depth: depth || 3,
    nodes: search.allNodes || 0
  };
}

// === HTTP Server ===
var server = http.createServer(function(req, res) {
  if (req.method === 'GET' && req.url === '/health') {
    res.writeHead(200, {'Content-Type': 'application/json'});
    res.end('{"status":"ok","engine":"xqwlight"}');
    return;
  }
  
  if (req.method === 'POST' && req.url === '/analyze') {
    var body = '';
    req.on('data', function(chunk) { body += chunk; });
    req.on('end', function() {
      try {
        var data = JSON.parse(body);
        var fen = data.fen;
        var depth = data.depth || 3;
        if (!fen) {
          res.writeHead(400, {'Content-Type': 'application/json'});
          res.end('{"error":"missing fen"}');
          return;
        }
        if (depth > 6) depth = 6;
        if (depth < 1) depth = 1;
        
        var result = analyze(fen, depth);
        res.writeHead(200, {'Content-Type': 'application/json'});
        res.end(JSON.stringify(result));
      } catch (e) {
        res.writeHead(500, {'Content-Type': 'application/json'});
        res.end(JSON.stringify({error: e.message || String(e)}));
      }
    });
    return;
  }
  
  res.writeHead(404);
  res.end('Not found');
});

var PORT = 8789;
server.listen(PORT, '127.0.0.1', function() {
  console.log('xqwlight engine server listening on 127.0.0.1:' + PORT);
});
