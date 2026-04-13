{{- define "openhound.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "openhound.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "openhound.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "openhound.labels" -}}
helm.sh/chart: {{ include "openhound.chart" . }}
app.kubernetes.io/name: {{ include "openhound.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "openhound.selectorLabels" -}}
app.kubernetes.io/name: {{ include "openhound.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "openhound.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "openhound.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{- define "openhound.validate" -}}
{{- if not .Values.collector.name -}}
{{- fail "collector.name is required" -}}
{{- end -}}
{{- if not .Values.config.existingConfigMap -}}
{{- fail "config.existingConfigMap is required" -}}
{{- end -}}

{{- range $name, $secret := .Values.collector.extraSecretMounts }}
  {{- if not $secret.secretName -}}
    {{- fail (printf "collector.extraSecretMounts.%s.secretName is required" $name) -}}
  {{- end -}}
  {{- if not $secret.secretKey -}}
    {{- fail (printf "collector.extraSecretMounts.%s.secretKey is required" $name) -}}
  {{- end -}}
  {{- if not $secret.mountPath -}}
    {{- fail (printf "collector.extraSecretMounts.%s.mountPath is required" $name) -}}
  {{- end -}}
{{- end -}}
{{- end -}}
