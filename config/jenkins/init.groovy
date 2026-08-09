import jenkins.model.*
import hudson.security.*
import jenkins.install.*

def instance = Jenkins.getInstance()
instance.setInstallState(InstallState.INITIAL_SETUP_COMPLETED)

def hudsonRealm = new HudsonPrivateSecurityRealm(false)
// 보안(CWE-798): 하드코딩 admin/admin 금지 — JENKINS_ADMIN_PASSWORD env 사용 (기본은 강력 랜덤)
def jenkinsPass = System.getenv("JENKINS_ADMIN_PASSWORD") ?: UUID.randomUUID().toString().replace("-", "") + "A1!"
hudsonRealm.createAccount('admin', jenkinsPass)
instance.setSecurityRealm(hudsonRealm)

def strategy = new FullControlOnceLoggedInAuthorizationStrategy()
strategy.setAllowAnonymousRead(false)
instance.setAuthorizationStrategy(strategy)

instance.save()
