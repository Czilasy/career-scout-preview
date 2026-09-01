import { mount } from "@vue/test-utils";
import TaskCompletedToast from "../TaskCompletedToast.vue";

describe("TaskCompletedToast", () => {
  it("renders nothing when not visible", () => {
    const wrapper = mount(TaskCompletedToast, { props: { visible: false } });
    expect(wrapper.find('[data-testid="task-completed-toast"]').exists()).toBe(false);
  });

  it("renders toast when visible and emits click on the main action", async () => {
    const wrapper = mount(TaskCompletedToast, { props: { visible: true } });
    expect(wrapper.find('[data-testid="task-completed-toast"]').exists()).toBe(true);
    await wrapper.get('[data-testid="task-completed-toast-go"]').trigger("click");
    expect(wrapper.emitted("click")).toHaveLength(1);
  });

  it("emits close when the close button is clicked", async () => {
    const wrapper = mount(TaskCompletedToast, { props: { visible: true } });
    await wrapper.get('[data-testid="task-completed-toast-close"]').trigger("click");
    expect(wrapper.emitted("close")).toHaveLength(1);
  });
});
