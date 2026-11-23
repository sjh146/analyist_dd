import jenkins.model.Jenkins
import org.jenkinsci.plugins.workflow.job.WorkflowJob
import org.jenkinsci.plugins.workflow.cps.CpsFlowDefinition
import hudson.triggers.TimerTrigger
import hudson.model.ParametersDefinitionProperty
import hudson.model.BooleanParameterDefinition
import hudson.model.StringParameterDefinition

def jenkins = Jenkins.get()
def scriptFile = new File('/var/jenkins_home/ci/Jenkinsfile.feed')
if (!scriptFile.isFile()) {
  println "[init] Jenkinsfile 없음: " + scriptFile
  return
}
def scriptText = scriptFile.getText('UTF-8')

def makeJob = { name, desc, withCron, allowStaleDefault ->
  def existing = jenkins.getItem(name)
  if (existing == null) {
    existing = jenkins.createProject(WorkflowJob.class, name)
  }
  existing.setDescription(desc)
  existing.setDefinition(new CpsFlowDefinition(scriptText, true))
  def params = new ParametersDefinitionProperty(
    new BooleanParameterDefinition('ALLOW_STALE', allowStaleDefault, '오래된 피드도 게시'),
    new StringParameterDefinition('VERSION', '', '코드 번들 버전(비우면 코드 게시 생략)')
  )
  existing.removeProperty(ParametersDefinitionProperty.class)
  existing.addProperty(params)
  existing.triggers.clear()
  if (withCron) {
    existing.addTrigger(new TimerTrigger('TZ=Asia/Seoul\n40 8 * * 1-5'))
  }
  existing.save()
  println "[init] job ready: " + name
}

makeJob('feed-publish', 'analyist_dd 피드+코드 게시(평일 08:40)', true, false)
makeJob('feed-publish-manual', '수동 복구용 게시', false, true)
println "[init] done"
