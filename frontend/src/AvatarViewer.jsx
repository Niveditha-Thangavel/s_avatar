// src/AvatarViewer.jsx

import { useEffect, useRef, useState } from "react"
import * as THREE from "three"
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader"
import { VRMLoaderPlugin, VRMUtils } from "@pixiv/three-vrm"
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls"

// ── Viseme scheduler ────────────────────────────────────────
class VisemeScheduler {
    constructor(vrm) {
        this.vrm        = vrm
        this.queue      = []
        this.audioStart = null
    }

    load(visemes, audioStartTime) {
        this.queue      = [...visemes]
        this.audioStart = audioStartTime
    }

    // Call this every animation frame
    update(now) {
        if (!this.audioStart || this.queue.length === 0) return

        const elapsed = (now - this.audioStart) / 1000   // seconds
        const proxy   = this.vrm.expressionManager

        // Find the current viseme
        const current = this.queue.find(v =>
            elapsed >= v.time && elapsed < v.time + v.duration
        )

        // Smoothly reset all mouth shapes
        const ALL_SHAPES = [
            "jawOpen","viseme_aa","viseme_PP","viseme_I",
            "viseme_E","viseme_O","viseme_U","viseme_FF","viseme_TH"
        ]
        ALL_SHAPES.forEach(s => {
            const curr = proxy.getValue(s) || 0
            proxy.setValue(s, curr * 0.6)   // smooth decay
        })

        // Apply current viseme blendshapes
        if (current) {
            Object.entries(current.blends).forEach(([shape, weight]) => {
                proxy.setValue(shape, weight)
            })
        }

        proxy.update()
    }
}

// ── Idle animation (blink + subtle head sway) ────────────────
class IdleAnimator {
    constructor(vrm) {
        this.vrm   = vrm
        this.t     = 0
        this.nextBlink = Math.random() * 3 + 2
    }

    update(delta) {
        this.t += delta
        const proxy = this.vrm.expressionManager

        // Blink
        if (this.t > this.nextBlink) {
            proxy.setValue("blink", 1.0)
            setTimeout(() => proxy.setValue("blink", 0.0), 150)
            this.nextBlink = this.t + Math.random() * 4 + 2
        }

        // Subtle breathing sway on VRM humanoid bones
        const neck = this.vrm.humanoid.getNormalizedBoneNode("neck")
        if (neck) {
            neck.rotation.y = Math.sin(this.t * 0.4) * 0.03
            neck.rotation.x = Math.sin(this.t * 0.3) * 0.01
        }

        proxy.update()
    }
}


export default function AvatarViewer() {
    const mountRef   = useRef(null)
    const vrmRef     = useRef(null)
    const schedRef   = useRef(null)
    const idleRef    = useRef(null)
    const [status, setStatus] = useState("Loading avatar...")
    const [text,   setText  ] = useState("")

    // ── Three.js scene setup ──────────────────────────────────
    useEffect(() => {
        const W = mountRef.current.clientWidth
        const H = mountRef.current.clientHeight

        const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true })
        renderer.setSize(W, H)
        renderer.setPixelRatio(window.devicePixelRatio)
        mountRef.current.appendChild(renderer.domElement)

        const scene  = new THREE.Scene()
        const camera = new THREE.PerspectiveCamera(35, W / H, 0.1, 100)
        camera.position.set(0, 1.4, 2.0)

        const controls = new OrbitControls(camera, renderer.domElement)
        controls.target.set(0, 1.4, 0)
        controls.update()

        // Lighting
        scene.add(new THREE.AmbientLight(0xffffff, 0.6))
        const dir = new THREE.DirectionalLight(0xffffff, 0.8)
        dir.position.set(1, 2, 2)
        scene.add(dir)

        // Load VRM
        const loader = new GLTFLoader()
        loader.register(parser => new VRMLoaderPlugin(parser))

        loader.load(
            "/avatar.vrm",      // put your VRM file in /public/
            gltf => {
                const vrm = gltf.userData.vrm
                VRMUtils.removeUnnecessaryJoints(gltf.scene)
                scene.add(vrm.scene)

                vrmRef.current  = vrm
                schedRef.current = new VisemeScheduler(vrm)
                idleRef.current  = new IdleAnimator(vrm)
                setStatus("Ready")
            },
            undefined,
            err => setStatus(`Error: ${err.message}`)
        )

        // Animation loop
        const clock = new THREE.Clock()
        let raf
        const animate = () => {
            raf = requestAnimationFrame(animate)
            const delta = clock.getDelta()
            const now   = performance.now()

            if (vrmRef.current) {
                idleRef.current?.update(delta)
                schedRef.current?.update(now)
                vrmRef.current.update(delta)
            }
            renderer.render(scene, camera)
        }
        animate()

        return () => {
            cancelAnimationFrame(raf)
            renderer.dispose()
            mountRef.current?.removeChild(renderer.domElement)
        }
    }, [])

    // ── Speak handler ─────────────────────────────────────────
    const speak = async () => {
        if (!text.trim() || !vrmRef.current) return
        setStatus("Generating...")

        const res  = await fetch("http://localhost:8000/speak", {
            method : "POST",
            headers: { "Content-Type": "application/json" },
            body   : JSON.stringify({ text, method: "rhubarb" })
        })
        const data = await res.json()

        // Decode + play audio
        const audioCtx = new AudioContext()
        const raw      = atob(data.audio_b64)
        const buf      = new Uint8Array(raw.length)
        for (let i = 0; i < raw.length; i++) buf[i] = raw.charCodeAt(i)

        const decoded  = await audioCtx.decodeAudioData(buf.buffer)
        const source   = audioCtx.createBufferSource()
        source.buffer  = decoded
        source.connect(audioCtx.destination)

        // Load visemes BEFORE audio starts → perfectly in sync
        schedRef.current.load(data.visemes, performance.now())
        source.start()

        source.onended = () => setStatus("Ready")
        setStatus("Speaking...")
    }

    return (
        <div style={{ display: "flex", flexDirection: "column",
                      height: "100vh", background: "#1a1a2e" }}>

            {/* 3D Viewport */}
            <div ref={mountRef} style={{ flex: 1 }} />

            {/* Status */}
            <div style={{ color: "#aaa", textAlign: "center",
                          fontSize: 12, padding: "4px" }}>
                {status}
            </div>

            {/* Controls */}
            <div style={{ display: "flex", gap: 8, padding: 16,
                          background: "#16213e" }}>
                <input
                    value={text}
                    onChange={e => setText(e.target.value)}
                    onKeyDown={e => e.key === "Enter" && speak()}
                    placeholder="Type what the avatar should say..."
                    style={{ flex: 1, padding: "10px 14px", borderRadius: 8,
                             border: "1px solid #444", background: "#0f3460",
                             color: "#fff", fontSize: 14 }}
                />
                <button
                    onClick={speak}
                    style={{ padding: "10px 24px", borderRadius: 8,
                             background: "#e94560", color: "#fff",
                             border: "none", cursor: "pointer",
                             fontSize: 14, fontWeight: "bold" }}
                >
                    Speak
                </button>
            </div>
        </div>
    )
}