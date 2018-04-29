node('master') {
    stage('Setup env'){
        TAG=sh(script: 'pwgen 5 1', returnStdout: true).trim()
        sh "python3.6 ~/start.py utils small-1 ${TAG} 5"
    }
}

pipeline {
    agent none
    options {
        skipDefaultCheckout()
    }
    stages {
        stage("Checkout & deploy") {
            when {
                branch 'master'
            }
            agent {
                node {
                    label "utils-small-1-${TAG}"
                }
            }
            steps {
                checkout scm
                sh 'python3.6 deploy.py'
            }
        }
    }
    post {
        always {
            node('master') {
                sh "python3.6 ~/stop.py ${TAG}"
            }
        }
    }
}