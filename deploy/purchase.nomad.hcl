variable "image" {
  type = string
}

variable "revision" {
  type = string
}

variable "namespace" {
  type = string
}

variable "hostname" {
  type = string
}

variable "runtime_variable" {
  type = string
}

job "purchase" {
  datacenters = ["cs403bkk"]
  namespace   = var.namespace
  type        = "service"

  meta {
    revision = var.revision
  }

  group "web" {
    count = 1

    constraint {
      attribute = "${node.unique.name}"
      value     = "cs403bkk-nomad-1"
    }

    update {
      max_parallel      = 1
      health_check      = "checks"
      min_healthy_time  = "10s"
      healthy_deadline  = "3m"
      progress_deadline = "5m"
      auto_revert       = true
    }

    network {
      port "http" {
        to = 8000
      }
    }

    task "app" {
      driver = "docker"

      config {
        image = var.image
        ports = ["http"]
      }

      env {
        APP_REVISION = var.revision
      }

      template {
        data                 = <<EOH
DATABASE_URL={{ with nomadVar "${var.runtime_variable}" }}{{ .database_url | toJSON }}{{ end }}
EOH
        destination          = "secrets/runtime.env"
        env                  = true
        perms                = "0600"
        change_mode          = "restart"
        error_on_missing_key = true
      }

      resources {
        cpu    = 300
        memory = 256
      }

      service {
        name     = "purchase"
        port     = "http"
        provider = "nomad"

        tags = [
          "traefik.enable=true",
          format("traefik.http.routers.purchase.rule=Host(`%s`)", var.hostname),
          "traefik.http.routers.purchase.entrypoints=websecure",
          "traefik.http.routers.purchase.tls=true",
          "traefik.http.routers.purchase.tls.certresolver=letsencrypt",
        ]

        check {
          type     = "http"
          path     = "/health"
          interval = "10s"
          timeout  = "2s"
        }
      }
    }
  }
}
