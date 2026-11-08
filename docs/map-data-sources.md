# مصادر طبقات الخريطة

- RainViewer API: https://www.rainviewer.com/api/weather-maps-api.html
  - endpoint: https://api.rainviewer.com/public/weather-maps.json
  - past radar frames: roughly two hours at 10-minute intervals when available
  - tile pattern: `{host}{path}/256/{z}/{x}/{y}/{color}/{smooth}_{snow}.png`
  - public API is intended for personal, educational and small community use; commercial/high-volume use needs review of terms and likely a bespoke agreement.
  - attribution required: Weather data by RainViewer with link to https://www.rainviewer.com/

- EUMETSAT WMS capabilities: https://view.eumetsat.int/geoserver/wms?service=WMS&version=1.3.0&request=GetCapabilities
  - cloud/satellite candidates include `msg_fes_ir108` (High Rate SEVIRI IR10.8um, 15-minute archive) and `mtg_fd_ir105_hrfi` (FCI IR10.5um, 10-minute archive).
  - WMS endpoint: https://view.eumetsat.int/geoserver/wms
  - satellite infrared shows cloud-top/thermal imagery; it is not ground radar precipitation.
