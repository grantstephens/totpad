# SPDX-License-Identifier: MIT
"""Minimal AES block encryption, used when the platform has no aesio.

Only the forward direction is implemented, because the envelope runs AES in
counter mode, where decryption is the same operation as encryption. This exists
so the host tooling and the test suite need no third-party dependency and can
exercise the same envelope code the device runs.
"""

_SBOX = bytearray(256)
_p = _q = 1
while True:  # generate the S-box rather than carrying a 256 byte literal
    _p = _p ^ ((_p << 1) & 0xFF) ^ (0x1B if _p & 0x80 else 0)
    _q ^= _q << 1
    _q ^= _q << 2
    _q ^= _q << 4
    _q &= 0xFF
    if _q & 0x80:
        _q ^= 0x09
    value = _q ^ ((_q << 1) | (_q >> 7)) ^ ((_q << 2) | (_q >> 6))
    value ^= ((_q << 3) | (_q >> 5)) ^ ((_q << 4) | (_q >> 4))
    _SBOX[_p] = (value ^ 0x63) & 0xFF
    if _p == 1:
        break
_SBOX[0] = 0x63

_RCON = [0x01]
for _i in range(1, 14):
    _RCON.append((_RCON[-1] << 1) ^ (0x11B if _RCON[-1] & 0x80 else 0) & 0xFF)
    _RCON[-1] &= 0xFF


def _xtime(a):
    a <<= 1
    if a & 0x100:
        a = (a ^ 0x1B) & 0xFF
    return a


class AES:
    """AES-128/192/256 single block encryption."""

    def __init__(self, key):
        if len(key) not in (16, 24, 32):
            raise ValueError("AES key must be 16, 24 or 32 bytes")
        self.nk = len(key) // 4
        self.rounds = self.nk + 6
        self.schedule = self._expand(key)

    def _expand(self, key):
        words = [list(key[4 * i : 4 * i + 4]) for i in range(self.nk)]
        for i in range(self.nk, 4 * (self.rounds + 1)):
            temp = list(words[i - 1])
            if i % self.nk == 0:
                temp = temp[1:] + temp[:1]
                temp = [_SBOX[b] for b in temp]
                temp[0] ^= _RCON[i // self.nk - 1]
            elif self.nk > 6 and i % self.nk == 4:
                temp = [_SBOX[b] for b in temp]
            words.append([words[i - self.nk][j] ^ temp[j] for j in range(4)])
        return words

    def encrypt_block(self, block):
        if len(block) != 16:
            raise ValueError("AES block must be 16 bytes")
        state = list(block)
        self._add_round_key(state, 0)
        for rnd in range(1, self.rounds):
            self._sub_shift(state)
            self._mix_columns(state)
            self._add_round_key(state, rnd)
        self._sub_shift(state)
        self._add_round_key(state, self.rounds)
        return bytes(state)

    def _add_round_key(self, state, rnd):
        for col in range(4):
            word = self.schedule[rnd * 4 + col]
            for row in range(4):
                state[col * 4 + row] ^= word[row]

    def _sub_shift(self, state):
        """SubBytes and ShiftRows in one pass over the column-major state."""
        sub = [_SBOX[b] for b in state]
        for row in range(1, 4):
            shifted = [sub[((col + row) % 4) * 4 + row] for col in range(4)]
            for col in range(4):
                sub[col * 4 + row] = shifted[col]
        state[:] = sub

    def _mix_columns(self, state):
        for col in range(4):
            base = col * 4
            a = state[base : base + 4]
            x = a[0] ^ a[1] ^ a[2] ^ a[3]
            state[base + 0] = a[0] ^ x ^ _xtime(a[0] ^ a[1])
            state[base + 1] = a[1] ^ x ^ _xtime(a[1] ^ a[2])
            state[base + 2] = a[2] ^ x ^ _xtime(a[2] ^ a[3])
            state[base + 3] = a[3] ^ x ^ _xtime(a[3] ^ a[0])
