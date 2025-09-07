document.addEventListener("DOMContentLoaded", function() {
    viewer = pannellum.viewer('panorama', {
        "type": "equirectangular",
        "panorama": "image.JPG",
        "autoLoad": true,
        "showControls": false,
        "compass": false
    })
    let isDragging = false;

    ['mousedown', 'touchstart'].forEach(ev => {
        viewer.on(ev, () => { isDragging = true; });
    });

    ['mouseup', 'touchend', 'touchcancel'].forEach(ev => {
        viewer.on(ev, () => { isDragging = false; });
    });

    function update() {
        if (isDragging) {
            const yaw = viewer.getYaw().toFixed(2);
            const pitch = viewer.getPitch().toFixed(2);
            const zoom = viewer.getHfov().toFixed(2);
            console.log(`{"yaw": ${yaw}, "pitch":${pitch}, "zoom":${zoom}}`)
        }
        requestAnimationFrame(update);
    }
    update();
    viewer.on('zoomchange', hfov => {
        const yaw = viewer.getYaw().toFixed(2);
        const pitch = viewer.getPitch().toFixed(2);
        const zoom = viewer.getHfov().toFixed(2);
        console.log(`{"yaw": ${yaw}, "pitch":${pitch}, "zoom":${zoom}}`)
    });
})
